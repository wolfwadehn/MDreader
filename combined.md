## CLAUDE

<a id="claude"></a>

| Jump to | Sections |
|---|---|
| Links | [CLAUDE](#claude), [LSTload](#lstload), [IOpBlockBuilder](#iopblockbuilder) |

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

Vulcan is a WPF/WinForms desktop application (.NET 10.0, x64) for laser machine control and CAM operations, built by TRUMPF Metamation. It parses and executes DIN/LST NC code files, drives Eckelmann and NCRX laser controllers, and provides a layout editor, job scheduler, and laser technology database.

## Build

The primary solution is `Src/Vulcan.sln`. All projects output to the shared `bin/` folder at the repo root.

```
dotnet build Src/Vulcan.sln
```

The Flux framework (a separate shared infrastructure library) must be available at `X:\Src\` — this is a mapped network/virtual drive to a sibling repo. CAMLib has a pre-build event that copies `Ijwhost.dll` and `armarium.ver` from `X:\src\bin\` into `bin\`.

Resource packaging (images, language files) is done separately via `makewad.bat`, which assembles `Res/` directories from source paths `L:\Src\Res`, `L:\CUILib\Res`, and `L:\CAMLib\Res` into `bin\Vulcan.wad`.

`EnterpriseVulcan.dll` is embedded as a compressed resource inside the main executable (`Vulcan.Resource.EV`) and loaded at runtime via a custom `AssemblyResolve` handler in `App.xaml.cs`.

## Tests

Tests use a **custom framework** (`Tools/TestLib/Testing.cs`) — not NUnit or xUnit. The runner is `Tools/CTester/CTester.exe`.

```
# Run all tests (from the bin/ directory)
CTester.exe -run

# Run specific test assemblies
CTester.exe -run VulcanTest.dll

# Regenerate expected output files instead of comparing
CTester.exe -regenerate VulcanTest.dll

# Run only fixtures marked with a given tag
CTester.exe -run -tag TAGNAME
```

CTester reads diff tool path from `c:\mm\mm.ini` under `[Paths] DIFF=`.

Test classes use `[TestFixture("name")]`; test methods use `[Test("description")]`. To run a single test in isolation, set `[Test(OnlyThis = true)]` on the method — this makes the runner skip all others. Skipped tests use `[Test(Skip = true)]`. Assertions include `Assert.AreEqual`, `Assert.AreTextFilesEqual`, and `Assert.AreImageFilesEqual` (PNG diff).

## Architecture

### Dependency layers (bottom to top)

```
Flux.*          — external framework at X:\Src\ (GUI, API, IniFile, Sys utilities)
LSTParser       — parses LST job files (layout+NC programs)
CAMLib          — core domain: NC parsing, machine control, comms, drawing
CUILib          — custom WPF/WinForms control library built on CAMLib
Vulcan          — main application UI
EnterpriseVulcan — enterprise feature add-in (embedded in Vulcan.exe at build time)
LaserTableConfigurator — standalone tool for editing the laser method database
```

`VulcanTest` references both `CAMLib` and `Vulcan` (for integration tests).

### CAMLib

The heart of the system. Key types:

- `NCCodeFile` (abstract) — factory via `LoadNCCodeFile(path)`. Converts LST→DIN on load. Subclassed by `DINFile`.
- `DINFile` — parses DIN-format NC programs block by block.
- `NCDrawing` / `NCContour` — geometry and drawing primitives for visualizing NC paths.
- `Controls.cs` — machine control state machine.
- `Machine.cs` / `MachineDef` — axis definitions and machine instance abstraction.
- `Comm/Eckelmann.cs`, `Comm/NCRXComm.cs` — controller communication drivers.
- `ProductionPlan.cs` — job scheduling and production sequencing.

### CUILib

Custom UI framework on top of CAMLib. The `Praxis/` sub-namespace (`PX.cs`, `Interface.cs`) integrates with the external Praxis MES/repository system for program checkout/check-in. `UILib.cs` provides shared utilities, theme constants, and file info persistence.

### Vulcan (main app)

Page-based navigation without a navigation controller — pages are instantiated and shown directly. Key pages:

- `HomePage.cs` — main dashboard, entry point
- `EditPage.cs` / `EditLST.cs` — NC/LST program editing
- `LayoutEditpage.cs` — nesting and layout editor
- `RunPage.cs` — active machine operation
- `SimulatePage.cs` — dry-run simulation
- `PopupWindow.cs` — large modal dialog hub (268 KB)

`MachineSettings/` contains ~13 files covering axis, PLC, technology, and display settings. `DINTextEditor/` contains a custom syntax lexer and editor panel for DIN code. `PostProduction/` handles LST nesting, camera integration, and post-processing.

## Naming Conventions

- Private instance fields: `mFieldName` (prefix `m`)
- Static fields: `sFieldName` (prefix `s`)
- Enums: `EEnumName` (prefix `E`), e.g. `EAxis`, `ESplMachineFeatures`, `EZRetract`
- New files must be manually added to the `.csproj` — `EnableDefaultCompileItems` is false in all projects.


## LSTload

<a id="lstload"></a>

| Jump to | Sections |
|---|---|
| Links | [CLAUDE](#claude), [LSTload](#lstload), [IOpBlockBuilder](#iopblockbuilder) |

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


## IOpBlockBuilder

<a id="iopblockbuilder"></a>

| Jump to | Sections |
|---|---|
| Links | [CLAUDE](#claude), [LSTload](#lstload), [IOpBlockBuilder](#iopblockbuilder) |

# IOpBlockBuilder

`IOpBlockBuilder` is the narrow writing interface between the NC program interpreter (`NcProgramContext`) and the geometry tree that represents one NC program block. Every motion command the interpreter encounters is funnelled through exactly one `IOpBlockBuilder` method call. When the interpreter finishes a program, calling `Completed()` seals the block and hands it to the tree.

Both the interface and the concrete implementation live in `LSTParser/`.

---

## Position in the call chain

```
NcConfig.InitOpTree(IOpTreeBuilder)
  └─ NcContext.BuildOpTree(IOpTreeBuilder)
       └─ NcProgramContext(…, IOpTreeBuilder)
            │  asks the tree builder for a block builder:
            │  mOpBlockBuilder = treeBuilder.CreateOpBlockBuilder(programName, isMain)
            │
            ├─ line by line: calls mOpBlockBuilder.AddLineTo / AddArcTo / …
            │
            └─ at end: mOpBlockBuilder.Completed(totalLines)
                         └─ produces HeadOpBlock, stored in OpTreeBuilder
```

Each NC program (main + every subroutine) gets its own `IOpBlockBuilder` instance.

---

## The companion: `IOpTreeBuilder`

```csharp
// LSTParser/NcProgram.cs
public interface IOpTreeBuilder {
    IOpBlockBuilder CreateOpBlockBuilder(string blockName, bool isMainBlock);
}
```

`IOpTreeBuilder` is the factory. `NcProgramContext` holds a reference to it and uses `CreateOpBlockBuilder` to obtain a fresh builder for each program it executes. The concrete implementation is `OpTreeBuilder` (`Job2.cs`), which accumulates the finished `HeadOpBlock` objects and exposes the main block as `ReconstructedMainBlock`.

---

## Interface declaration

```csharp
// LSTParser/NcProgram.cs  (line 901)
public interface IOpBlockBuilder {
    void AddLineTo     (int lineNumber, double toX, double toY, OpInfoBits infoBits);
    void AddArcTo      (int lineNumber, double toX, double toY,
                        double centerX, double centerY, OpInfoBits infoBits);
    void AddPunchLineTo(int lineNumber, double toX, double toY,
                        string punchId, double punchAngle, int punchCount, OpInfoBits infoBits);
    void AddPunchArcTo (int lineNumber, double toX, double toY,
                        double centerX, double centerY,
                        string punchId, double punchAngle, int punchCount, OpInfoBits infoBits);
    void AddOpBlockRef (int lineNumber, string blockName,
                        double atX, double atY, double rotation,
                        bool beginsPart, List<string> lbls);
    void Completed          (int totalLines);
    void SetHeadActiveLine  (int lineNumber);
    void SetHeadInactiveLine(int lineNumber);
    void SetPartEntryLine   (int lineNumber);
}
```

---

## Methods

### `AddLineTo`

```csharp
void AddLineTo(int lineNumber, double toX, double toY, OpInfoBits infoBits)
```

Records a linear head movement. Called whenever a motion line has at least one of X or Y but the G-code is **not** G02/G03, or the G-code is G02/G03 but has no I/J (treated as linear). Also called for G00 rapid moves (with `LaserOn = false`).

`toX`, `toY` are world coordinates in mm, already transformed by the active `NcCoordXfm` before this method is called.

**Produces:** `HeadOpUnit.CreateLinearToOp(pt, isLaserOn, isBeamSuppressionOn, gotEntryLaserHint, lineNumber, zBase)`

- `IsMoveOp = true` when `LaserOn` bit is absent — a non-cutting rapid move.
- `IsBeamOff = true` when `G823` bit is set (`LaserOn` must also be set) — beam suppression during a cut (fly-line, micro-joint traverse).

---

### `AddArcTo`

```csharp
void AddArcTo(int lineNumber, double toX, double toY,
              double centerX, double centerY, OpInfoBits infoBits)
```

Records a circular arc movement. Called when G02 or G03 is present and I and/or J are present on the same line.

`centerX`, `centerY` are world-coordinate arc centre points (not the raw I/J increments — the interpreter converts them: `center = lastHeadPos + xfm.TransformVectorXY(I, J)` before calling).

The `Ccw` bit distinguishes G03 (CCW) from G02 (CW).  
The `Circle` bit is set when there is no X/Y endpoint on the line (classic full-circle syntax `G03 I3.326`) or when the X/Y displacement evaluates to zero in G91 mode (`G03 X0 Y0`).

**Produces:** `HeadOpUnit.CreateArcToOp(pt, center, isCcw, isCircle, isLaserOn, isBeamSuppressionOn, gotEntryLaserHint, lineNumber, zBase)`

---

### `AddPunchLineTo`

```csharp
void AddPunchLineTo(int lineNumber, double toX, double toY,
                    string punchId, double punchAngle, int punchCount, OpInfoBits infoBits)
```

Records a linear punch hit sequence. Called instead of `AddLineTo` when `isPunchOn` or `isNibbleOn` is true at the time a motion line is encountered.

`punchId` is the tool identifier last set by `TC_TOOL_LOAD`. `punchAngle` is always `0` (not currently extracted from the NC line). `punchCount` is always `1` per call.

The `RotatePunch` bit on `infoBits` signals that the tool should rotate to follow the travel direction.

**Produces:** `PunchOp.CreateLineHitOp(toPt, punchId, punchAngle, punchCount, rotatePunch, partBegins, lineNumber, zBase)`

---

### `AddPunchArcTo`

```csharp
void AddPunchArcTo(int lineNumber, double toX, double toY,
                   double centerX, double centerY,
                   string punchId, double punchAngle, int punchCount, OpInfoBits infoBits)
```

Records a circular arc punch hit sequence. Called instead of `AddArcTo` when punch/nibble is active.

Same arc semantics as `AddArcTo` for the geometry. Same punch semantics as `AddPunchLineTo` for the tool fields.

**Produces:** `PunchOp.CreateArcHitOp(toPt, center, isCcw, isCircle, punchId, punchAngle, punchCount, rotatePunch, partBegins, lineNumber, zBase)`

---

### `AddOpBlockRef`

```csharp
void AddOpBlockRef(int lineNumber, string blockName,
                   double atX, double atY, double rotation,
                   bool beginsPart, List<string> lbls)
```

Records a subroutine call. Called after `NcContext.BuildSubprogram()` has already parsed and completed the referenced subroutine, so the corresponding `HeadOpBlock` already exists in `OpTreeBuilder.mSubBlocks`.

`atX`, `atY` is the head position at the moment of the call (in the caller's frame). `rotation` is `xfm.FrameRotation` at the call site. `lbls` is the list of `TC_LABEL` strings accumulated since the last call or part entry.

The concrete implementation skips the call silently if the referenced block has no operations (empty subroutine).

Advancing the `mZBase` counter by `block.ColorZLength` and `mLineBase` by `block.LinesDeepcount - 1` ensures that subsequent ops in the calling block receive globally-unique Z-colour indices and absolute line numbers.

**Produces:** `new HeadOpBlockRef(block, new Point(atX, atY), rotation, lineNumber, mZBase, beginsPart, lbls)`

---

### `Completed`

```csharp
void Completed(int totalLines)
```

Seals the block. Called exactly once per `NcProgramContext.Execute()` invocation, at the very end, after all lines have been processed.

`totalLines` is the total physical line count of the program as reported by `TextLineItr.ItemCount`.

The concrete implementation:
1. Constructs `HeadOpBlock(blockName, mOps, totalLines, partEntryLines, headOnLines, headOffLines)` and hands it to `OpTreeBuilder.AddOpBlock(block, isMain)`.
2. If the block is the main program and at least one part-entry line was recorded, appends one final sentinel entry at `totalLines` — this closes the last implicit part range so `GetContainingPartReentryPartLineRange` always finds a bounded interval.

---

### `SetHeadActiveLine`

```csharp
void SetHeadActiveLine(int lineNumber)
```

Marks the line number of a laser or punch activation command (`TC_LASER_ON`, `TC_LASER_MACHINING_ON`, `PUNCH_ON`, `NIBBLE_ON`). Called by the interpreter immediately when one of those literals is recognised.

Only the **first** `SetHeadActiveLine` call after the preceding `SetHeadInactiveLine` is recorded (`mGotHeadOnLine` guard). This is intentional: `TC_LASER_ON` can be preceded by several preparatory sub-calls in the LST and the guard ensures only the outermost activation line is captured.

The stored line number is absolute: `mLineBase + lineNumber` (block-local line converted to global).

**Populates:** `HeadOpBlock.HeadOnLines` — used by `GetLaserOnLineNumber()` to locate the activation point preceding any given absolute line.

---

### `SetHeadInactiveLine`

```csharp
void SetHeadInactiveLine(int lineNumber)
```

Marks the line number of a deactivation command (`TC_LASER_OFF`, `PUNCH_OFF`, `NIBBLE_OFF`). Resets `mGotHeadOnLine` so the next `SetHeadActiveLine` is accepted.

**Populates:** `HeadOpBlock.HeadOffLines` — used by `GetLaserOffLineNumber()`.

---

### `SetPartEntryLine`

```csharp
void SetPartEntryLine(int lineNumber)
```

Marks the line number of an `ENTRY_LASER:` or `ENTRY_PUNCH:` comment. These comments appear in the main program immediately before the first cutting move of each part. Called by the interpreter when the comment is encountered; the interpreter also sets `partBegins = true` so the next `AddLineTo`/`AddArcTo` call carries the `BeginsPart` flag.

Only called for the main program (the interpreter skips this in subroutines).

**Populates:** `HeadOpBlock.PartEntryLines` — used by `GetContainingPartReentryPartLineRange()` to find the line range belonging to the part currently being cut.

---

## `OpInfoBits` — the flag register

```csharp
// LSTParser/NcProgram.cs  (line 922)
[Flags]
public enum OpInfoBits : ushort {
    Ccw         = 0x001,   // G03 — arc is counter-clockwise
    Circle      = 0x002,   // Full circle (no endpoint / zero displacement)
    LaserOn     = 0x004,   // isLaserOn was true when the motion was emitted
    G823        = 0x008,   // Beam suppression flag (fly-line / micro-joint traverse)
    BeginsPart  = 0x010,   // partBegins was true (main program only)
    RotatePunch = 0x100,   // Punch tool rotates to follow arc direction
}
```

`OpInfoBits` is assembled by `NcProgramContext.Execute()` immediately before each builder call, using the current interpreter state:

| Bit | Set when |
|---|---|
| `Ccw` | `ncG == 3` |
| `Circle` | No X/Y on line, or G91 X0 Y0 |
| `LaserOn` | `isLaserOn == true` |
| `G823` | `G823` appeared on the same NC line |
| `BeginsPart` | `partBegins == true` **and** this is the main program |
| `RotatePunch` | `A` or `B` word present (not currently extracted, always 0 in practice) |

Extension method for testing: `infoBits.HasBit(OpInfoBits.Ccw)` (defined in `OpBitsUtil`).

---

## Concrete implementation: `OpBlockBuilder`

`OpBlockBuilder` (`LSTParser/Job2.cs`) is the only implementation and is not `public`. It is instantiated exclusively by `OpTreeBuilder.CreateOpBlockBuilder`.

### Internal state

| Field | Type | Purpose |
|---|---|---|
| `mOps` | `List<HeadOp>` | Accumulates ops in program order |
| `mZBase` | `int` | Global Z-colour index, incremented with each unit op; advanced by `block.ColorZLength` on each `AddOpBlockRef` |
| `mLineBase` | `int` | Global line-number offset, advanced by `block.LinesDeepcount - 1` on each `AddOpBlockRef` |
| `mPartEntryLines` | `List<int>` | Absolute line numbers from `SetPartEntryLine` |
| `mHeadOnLines` | `List<int>` | Absolute line numbers from `SetHeadActiveLine` |
| `mHeadOffLines` | `List<int>` | Absolute line numbers from `SetHeadInactiveLine` |
| `mGotHeadOnLine` | `bool` | Guards against recording duplicate consecutive ON lines |

### Z-colour index

Every `HeadOpUnit` and `HeadOpBlockRef` carries a `ColorZBase` integer that uniquely identifies its position in the cutting sequence across all blocks. When the renderer draws the program with a colour gradient (depth-coded "rainbow" view), it uses this index. `mZBase` is incremented by 1 for each unit op and by `block.ColorZLength` for each block reference, ensuring there are no gaps or collisions even when subroutines are called multiple times from different call sites.

### What `Completed()` produces

```csharp
public void Completed(int totalLines) {
    mTreeBuilder.AddOpBlock(
        new HeadOpBlock(mBlockName, mOps, totalLines,
                        mIsMain ? mPartEntryLines : null,
                        mHeadOnLines, mHeadOffLines),
        mIsMain);
    if (mIsMain && mPartEntryLines.Any())
        SetPartEntryLine(totalLines); // sentinel for last part range
}
```

`PartEntryLines` is only passed to `HeadOpBlock` for the main program; subroutine blocks receive `null` because part-boundary tracking is a main-program concept.

---

## Summary: one builder call per NC line

| NC line type | Builder method | Resulting `HeadOp` |
|---|---|---|
| `G01 X… Y…` (laser on) | `AddLineTo` | `HeadOpUnit` — `IsMoveOp=false` |
| `G00 X… Y…` (rapid) | `AddLineTo` | `HeadOpUnit` — `IsMoveOp=true` |
| `G01 X… Y…` with G823 | `AddLineTo` | `HeadOpUnit` — `IsBeamOff=true` |
| `G02/G03 X… I… J…` | `AddArcTo` | `HeadOpUnit` — `IsArc=true` |
| `G03 I…` (no X/Y) | `AddArcTo` | `HeadOpUnit` — `IsCircle=true` |
| Any motion while punch active | `AddPunchLineTo` / `AddPunchArcTo` | `PunchOp` |
| Subroutine name literal | `AddOpBlockRef` | `HeadOpBlockRef` |
| `TC_LASER_ON` | `SetHeadActiveLine` | — (metadata only) |
| `TC_LASER_OFF` | `SetHeadInactiveLine` | — (metadata only) |
| `;ENTRY_LASER:` comment | `SetPartEntryLine` | — (metadata only) |
| End of program | `Completed(totalLines)` | seals → `HeadOpBlock` |
