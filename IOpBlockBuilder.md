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
