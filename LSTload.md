# LST File Loading Pipeline

A `.lst` file is never executed directly. Loading one triggers a two-stage process: parse the LST into an in-memory operation tree, then transpile that tree to a `.din` file on disk, which is finally parsed as a normal DIN program. The result is an `NCCodeFile` (concretely a `DINFile`) together with a set of `LSTPart` objects extracted in parallel.

---

## Entry Point

```csharp
// CAMLib/NCCodeFile.cs
public static NCCodeFile LoadNCCodeFile(string filename, bool iEditmode = false)
```

When `Path.GetExtension(filename)` is `".lst"`, this method calls `ProcessLST(filename)`, which returns a path to a generated `.din` file. That path is then loaded with `new DINFile(dinPath, iEditmode)` and returned.

The generated DIN files live at:
```
{Lib.ProgramsPath}/DINs/{filenameWithoutExtension}.din
```

---

## Stage 1 — Structural Parsing: `NcConfig.Create()`

```csharp
// LSTParser/NcProgram.cs
public static NcConfig Create(string lstContent)
```

This is the lowest-level LST parser. It reads the entire file as a string and produces a `NcConfig` object holding all tables and NC program listings.

### Line iteration: `LstItr`

`LstItr` (`LSTParser/Lst.cs`) wraps a raw line sequence and handles **line continuations**:
- A line starting with `* ` (space or tab) is a soft continuation — the `* ` prefix is stripped and the text is appended to the previous line.
- A line starting with `- ` is a value-split continuation — the opening/closing single-quote boundary of a CSV string value is spliced back together.

`LstItr` exposes `.LineNumber` (tracks the first physical line of each logical line) and `.ItemCount`.

### Table blocks

All lines matching `BEGIN_TABLENAME … ENDE_TABLENAME` are collected into `LstTable` objects and stored in `NcConfig.mTables`. Common table names:

| Table name | Content |
|---|---|
| `PROGRAMM` | Program list (name, type HP/UP, part index) |
| `SHEET_TECH` | Sheet X/Y/Z dimensions, material ID |
| `SHEET_LOAD_DATA` | Suction-cup groups, peel type, double-sheet detector |
| `MACHINE_LOAD_DATA` | Machine zero-point offsets |
| `EINRICHTEPLAN_INFO` | Program name, machine name, operator, estimate time |
| `PARTS_IN_PROGRAM` | Part definitions (ID, bounding box) |
| `PARTS_IN_PROGRAM_POS` | Part placements (position, rotation) |
| `WZG_STAMM` / `PTT` | Punch tool definitions |
| `LTT_STAMM` / `LTT_CALLS` | Laser tool/material references |
| `MICROJOINT` | Micro-joint widths per tool |
| `FERTIGUNG_AUFTRAG_TMP` | Production plan rows (used for multi-job LSTs) |
| `TUBE_TECH` | Tube workpiece specifications |

`LstTable` stores column definitions (`List<ColumnInfo> mColumns`) and raw rows (`List<string[]> mRowCells`). Data is accessed via:
- `GetS(row, columnId)` — string
- `GetF(row, columnId)` — `double`
- `GetMM(row, columnId)` — `double`, auto-converts inches to mm

### NC program listings

Within the `PROGRAMM` table block, each program is delimited by `START_TEXT … STOP_TEXT`. Lines between those markers are collected as `List<string>` and stored in `NcConfig.mNcListings` (a `Dictionary<string, List<string>>`). The PROGRAMM table row with type `"HP"` identifies the main program; rows with type `"UP"` are subroutines. `mMainProgramName` holds the name of the HP entry.

### Sheet dimensions

`NcConfig.SheetX`, `SheetY`, `SheetZ` are read from `SHEET_TECH` columns 20, 30, 40.  
`NcConfig.ZeroPointX`, `ZeroPointY` come from `SHEET_LOAD_DATA` or `MACHINE_LOAD_DATA`.  
If `SheetX` or `SheetY` is `NaN` after parsing, `JobUtil2.CreateJob` throws `FileFormatException("Missing sheet size")`.

---

## Stage 2 — Operation Tree: `NcConfig.InitOpTree()` → `NcContext.BuildOpTree()`

```csharp
// NcProgram.cs
public void InitOpTree(IOpTreeBuilder treeBuilder, ILogger logger)
```

This drives the NC program interpreter. An `OpTreeBuilder` (defined in `Job2.cs`) is passed in as the `IOpTreeBuilder`. After this call, `treeBuilder.ReconstructedMainBlock` holds the complete geometry tree.

### `NcContext`

`NcContext` owns the full listing map. Its `BuildOpTree()`:
1. Creates a `NcProgramContext` for the main program.
2. Calls `Execute()` on it.
3. Records whether the main program uses absolute dimensioning (`IsMainPrgAbsolute`).

### `NcProgramContext.Execute()`

This is the core NC interpreter (`NcProgram.cs`, line 120). It iterates lines via `TextLineItr`, tokenises each with `NcItem.Parse(line)`, and maintains the following state:

| State variable | Purpose |
|---|---|
| `headPositionX/Y` | Current laser head position in world coordinates (mm) |
| `isLaserOn` | `TC_LASER_ON` / `TC_LASER_MACHINING_ON` sets this; `TC_LASER_OFF` clears it |
| `isPunchOn` / `isNibbleOn` | Punch tool active state |
| `mUseAbsoluteDimension` | `G90` = absolute, `G91` = incremental |
| `mUnitScale` | 1.0 (metric) or 25.4 (inch, set by `G70`/`G71`) |
| `xfm: NcCoordXfm` | Active coordinate frame (translation + rotation) |
| `partBegins` | Raised on `ENTRY_LASER:` / `ENTRY_PUNCH:` comment; cleared after first laser-on move |

#### Token types produced by `NcItem.Parse()`

| Type | Examples | Handling |
|---|---|---|
| `NcWord` | `X`, `Y`, `I`, `J`, `G`, `A`, `B`, `Z`, `F`, `M` | Extracted into `ncX`, `ncY`, `ncI`, `ncJ`, `ncG`; unit-scaled; `mBits` flags set |
| `Comment` | `;ENTRY_LASER:` | Sets `partBegins = true`; calls `SetPartEntryLine()` |
| `Literal` | `TRANS`, `ATRANS`, `PUNCH_ON`, `NIBBLE_ON`, subprogram name | Frame manipulation or subroutine dispatch |
| `NcROT` / `NcAROT` | `ROT RPL=90`, `AROT RPL=45` | Updates `xfm` rotation |
| `SubCall` | `TC_LASER_ON(…)`, `TC_TOOL_LOAD(…)`, `TC_SHEET_REPOSIT(…)` | Sets `isLaserOn`, `PunchId`, repositions frame |

#### Coordinate transformation

`NcCoordXfm` is immutable (`LSTParser/NcProgram.cs`). Every frame command creates a new instance:

- `xfm.Translated(dx, dy, isAdditive)` — `TRANS` resets, `ATRANS` adds
- `xfm.Rotated(angle, isAdditive)` — `ROT` resets, `AROT` adds
- `xfm.Appended(other)` — composes two transforms (used when a subroutine modifies the frame)

Absolute positions are transformed with `xfm.TransformPointXY(ref x, ref y)` (applies rotation then translation). Relative vectors (arc `IJ` deltas) use `xfm.TransformVectorXY(ref x, ref y)` (rotation only, no translation).

The zero-point offset from `SHEET_LOAD_DATA` is baked in at the start of the main program:
```csharp
xfm = xfm.Translated(-ncConfig.ZeroPointX, -ncConfig.ZeroPointY, false);
```

#### Motion dispatch

After all NC words on a line are collected:

| Condition | `IOpBlockBuilder` call |
|---|---|
| `G02`/`G03` with `I`/`J` present | `AddArcTo(lineNum, toX, toY, centerX, centerY, bits)` |
| `G02`/`G03` with no X/Y, or X/Y displacement = 0 | Same, plus `OpInfoBits.Circle` |
| Has `X` or `Y` (linear) | `AddLineTo(lineNum, toX, toY, bits)` |
| Punch/nibble active + arc | `AddPunchArcTo(...)` |
| Punch/nibble active + linear | `AddPunchLineTo(...)` |

`OpInfoBits` flags carried per operation:

| Flag | Source |
|---|---|
| `LaserOn` | `isLaserOn` at time of move |
| `G823` | `G823` present on same line (beam suppression / fly-line) |
| `BeginsPart` | `partBegins` was set (main program only) |
| `Ccw` | `G03` |
| `Circle` | Full-circle arc condition |

#### Subroutine dispatch

When a bare `Literal` token matches a known subprogram name (`NcContext.IsSubProgramName()`):

1. `NcContext.BuildSubprogram(this, name)` is called.
2. A new `NcProgramContext` is constructed inheriting `mUseAbsoluteDimension`, `mUnitScale`, and `PunchId` from the caller.
3. `Execute()` runs on the subroutine listing.
4. The return value is `Tuple<dispX, dispY, dimMode, dimModeReset, xfmOrNull>` — the head displacement and any frame changes the subroutine made.
5. Back in the caller, `mOpBlockBuilder.AddOpBlockRef(lineNum, name, headX, headY, xfm.FrameRotation, partBegins, labels)` records a reference to the subroutine's block.
6. In incremental mode, the caller's head position is advanced by the subroutine's displacement.

Each subroutine is executed only once (memoised in `mHeadDisplacementMap`). The maximum call depth is 8.

---

## Stage 3 — `OpTreeBuilder` and Result Objects

`OpBlockBuilder` (`Job2.cs`) accumulates operations into a list and, on `Completed(totalLines)`, hands a `HeadOpBlock` to `OpTreeBuilder`. The main block is stored in `ReconstructedMainBlock`; subroutine blocks go into `mSubBlocks`.

### `HeadOpBlock`

```csharp
class HeadOpBlock {
    string Name;                // Program/block name
    List<HeadOp> HeadOps;      // Operations in program order
    int TotalLines;             // Total NC lines in block
    List<int> PartEntryLines;  // Abs line numbers of ENTRY_LASER/ENTRY_PUNCH (main only)
    List<int> HeadOnLines;     // Abs line numbers of first LASER_ON per cut
    List<int> HeadOffLines;    // Abs line numbers of LASER_OFF
    int ColorZLength;          // Depth for Z-color visualisation (= HeadOps.Count)
}
```

`HeadOp` is abstract. The three concrete types:

### `HeadOpUnit` — a single motion segment

```csharp
class HeadOpUnit : HeadOp {
    Point ToPt;                  // World-coordinate end point (mm)
    Point ArcCenter;             // Arc centre (mm), meaningful only when IsArc
    bool IsMoveOp;               // true = non-cutting rapid (laser off)
    bool IsArc;                  // false = line, true = arc
    bool IsCcw;                  // Arc direction
    bool IsCircle;               // Full circle
    bool IsBeamOff;              // G823 beam-suppression flag
    bool IsEntryLaserHinted;     // Was BeginsPart set when this op was emitted
    int LineNumber;              // Block-local source line number
    int AbsLineNumber;           // Absolute line number across all blocks
    int ColorZIndex;             // Sequential index for Z-colour rendering
}
```

### `PunchOp : HeadOpUnit` — a punch hit

```csharp
class PunchOp : HeadOpUnit {
    string PunchId;    // Punch tool identifier (from TC_TOOL_LOAD)
    float PunchAngle;  // Rotation at hit time
    int PunchCount;    // Number of hits
}
```

### `HeadOpBlockRef : HeadOp` — a subroutine call

```csharp
class HeadOpBlockRef : HeadOp {
    HeadOpBlock Block;      // The referenced subroutine block
    Point Pos;              // Call-site head position (world coordinates, mm)
    double Rotation;        // Frame rotation at call site (degrees)
    NcCoordXfm Xfm;        // Full coordinate transform at call site
    List<string> Labels;   // TC_LABEL strings collected before this call
    bool BeginsPart;       // partBegins was true at call site
    int ColorZLength;      // Delegated from Block.ColorZLength
}
```

---

## Stage 4 — `JobUtil2.CreateJob()`

```csharp
// LSTParser/Job2.cs
public static Job CreateJob(
    string lstContent,
    out List<Tuple<string, bool, bool, List<string>>> programs,
    out List<LstTable> tables,
    out bool isInch,
    out bool iSheetMeasure,
    out bool iMainPrgAbsolute)
```

This is the public assembly point called from `ProcessLST`. It:

1. Calls `NcConfig.Create(lstContent)`.
2. Creates an `OpTreeBuilder` and calls `ncConfig.InitOpTree(treeBuilder, logger)`.
3. Reads `treeBuilder.ReconstructedMainBlock`.
4. Constructs `Sheet(sheetX, sheetY, sheetZ)` and `Workitem(sheet, headOpBlock)`.
5. Calls `ncConfig.GetNcPrgListings()` → populates `programs` out-param (each entry: `(name, isMain, isAbsolute, lines)`).
6. Adds punch tools: `StdPunch` if the tool has geometry parameters, `CustomPunch` otherwise.
7. Returns `ncConfig.GetTables()` as `tables`, plus `isInch` and `iMainPrgAbsolute` flags.

### `Job`

```csharp
sealed class Job {
    Workitem Workitem;          // Sheet geometry + op tree
    double mZeroOffsetX;       // Null-point X offset from SHEET_LOAD_DATA
    List<IPunch> mPunches;     // Punch tool palette
}
```

### `Workitem`

```csharp
class Workitem {
    Workpiece Workpiece;        // Sheet or Tube with Width, Height, Thickness
    HeadOpBlock HeadOpBlock;   // Root of the operation tree
}
```

`Sheet : Workpiece` (type `Types.Sheet`) holds the flat-sheet dimensions.  
`Tube : Workpiece` (type `Types.Tube`) is used when `TUBE_TECH` is present.

---

## Stage 5 — Part Extraction: `LoadParts()`

Running in parallel with DIN generation inside `ProcessLST`:

```csharp
// CAMLib/NCCodeFile.cs
static void LoadParts(HeadOpBlock block, string fileName)
```

This populates the static `sParts: List<LSTPart>`. For each `HeadOpBlockRef` in `block.HeadOps` it creates an `LSTPart` named after the referenced block and calls `ConsumeSubPgm(bref.Block, part)` to fill it with geometry.

`ConsumeSubPgm` walks every `HeadOp`:
- `HeadOpUnit` with `IsArc` → `pline.Add(ptPrev, arcCenter, isCCW)` then `pline.Add(pt)`.
- `HeadOpUnit` with `IsMoveOp` → finalises the current `Pline` and starts a new one.
- `HeadOpUnit` linear → `pline.Add(ptPrev)` then `pline.Add(pt)`.
- Nested `HeadOpBlockRef` → recursive `ConsumeSubPgm` into a child `LSTPart`.

`LSTPart` (`CAMLib/LSTPart.cs`):
```csharp
class LSTPart {
    string Name;
    List<Pline> mPlines;    // Continuous geometric paths
    List<string> SubPgms;  // Names of child part blocks
}
```

---

## Stage 6 — DIN Code Generation: `ProcessPgm()` in `ProcessLST()`

After `LoadParts`, `ProcessLST` writes the DIN header and then calls `ProcessPgm` for the main program and each subroutine to produce DIN lines in `contents: List<string>`.

### DIN header (from EINRICHTEPLAN_INFO and SHEET_TECH tables)

```
%1(PROGRAM_NAME.DIN)
(MACHINE: machineName)
(USER: operatorName)
MachiningTime = estimateTime
Sheet_Size_X = x
Sheet_Size_Y = y
G90  (or G91)
M15
G17 D=SheetRotation
CheckBoundPrg
```

### Per-program LST → DIN command mapping

| LST construct | DIN output | Notes |
|---|---|---|
| `TC_LASER_ON(params)` | `N{n} M1014` + `TC_UPDATE_PARAM R[CutParams;CMValue;MJLen;NJLen]` + `TC_LASER_ON R[params]` | `CMValue` encodes G823 (bit 0), G821/MJ (bit 1), G824/NJ (bit 2) |
| `TC_LASER_MACHINING_ON` | Same as `TC_LASER_ON` | |
| `TC_LASER_OFF` | `N{n} M1015` | Laser-off marker |
| `G01/G02/G03 X Y I J` | Direct NC copy | Coordinates already in mm |
| `TC_SHEET_TECH(…)` | `TC_SHEET_THICK(…)` | Renamed |
| `TC_SHEET_LOAD` | skipped | `skipInfo` list |
| `;GOTOF ENTRY_LASER` | skipped | `skipInfo` list |
| `ENTRY_LASER:` | skipped | `skipInfo` list |
| `TC_PART_END` | skipped | `skipInfo` list |
| `G823` (fly-line) | `(G823)` | Wrapped in comment |
| `G821` (micro-joint) | `(G821)` | Wrapped in comment |
| `G824` (nano-joint) | `M1030` or `M1031` | Converted to motion code |
| `ROT RPL=angle` | `G89 C={angle}` | Rotation command |
| `TRANS / ATRANS` | Applied to output coordinates | |
| Subprogram call name | `G22 J{blockNumber}` | Block number assigned during output |
| `TC_WAIT(…)` | `TC_Wait R[…]` | |
| `MSG(text)` | `G253 text` | |
| `G04 X{t}` | `G04 X{t}` | Dwell passthrough |
| `TC_POS_LEVEL` | `TC_POS_LEVEL` + following move | `arr` group — output with context |
| `TC_LASER_OFF` | also in `arr` group | |

`scM1014` and `scDuplicates` are matched at the end; a mismatch throws `ExceptionType.M1014SyncError`.

The file ends with:
```
N2147483647
CallBoundPrg
G99
```

---

## Stage 7 — DIN Parsing: `DINFile`

Once `ProcessLST` writes the `.din` file and returns its path, `LoadNCCodeFile` does:

```csharp
ncCode = new DINFile(filename, iEditmode);
```

`DINFile : NCCodeFile` (`CAMLib/DINFile.cs`) parses the DIN format and populates the `NCCodeFile` properties available to callers:

| Property | Populated from |
|---|---|
| `SheetSize` | `Sheet_Size_X/Y` header lines |
| `Contours[]` | `NCContour` objects built from G01/G02/G03 sequences |
| `Drawing` | `NCDrawing` built from contours |
| `Lines[]` | Raw DIN source lines |
| `Name` | `Path.GetFileNameWithoutExtension(lstFileName)` |
| `FilePath` | Path of the generated `.din` file |
| `UnHandledCodes` | Set of LST codes that had no DIN mapping |

`NCCodeFile.LoadNCCodeFile` also calls `ncCode.CheckForRemnant()` after construction and, if `ContainsNJ` (nano-joint) is set, re-parses the DIN a second time.

---

## Object Summary

| Object | Defined in | Role |
|---|---|---|
| `NcConfig` | `LSTParser/NcProgram.cs` | Raw parse result: tables + NC listings |
| `LstTable` | `LSTParser/Lst.cs` | One `BEGIN_…ENDE_` block with typed cell access |
| `NcCoordXfm` | `LSTParser/NcProgram.cs` | Immutable frame transform (translate + rotate) |
| `Job` | `LSTParser/Job.cs` | Top-level result: workitem + punch palette + zero offset |
| `Workitem` | `LSTParser/Job.cs` | Sheet + root `HeadOpBlock` |
| `Sheet / Tube` | `LSTParser/Job.cs` | Workpiece dimensions |
| `HeadOpBlock` | `LSTParser/Job.cs` | Ordered list of `HeadOp`s for one NC program |
| `HeadOpUnit` | `LSTParser/Job.cs` | Single line or arc segment |
| `PunchOp` | `LSTParser/Job.cs` | Punch hit (extends `HeadOpUnit`) |
| `HeadOpBlockRef` | `LSTParser/Job.cs` | Subroutine call-site with position and transform |
| `OpTreeBuilder` | `LSTParser/Job2.cs` | Assembles `HeadOpBlock` tree from interpreter calls |
| `LSTPart` | `CAMLib/LSTPart.cs` | Geometry (`Pline` list) for one part shape |
| `NCCodeFile / DINFile` | `CAMLib/NCCodeFile.cs / DINFile.cs` | Final result returned to the application |
