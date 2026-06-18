# LST Loading — Diagrams

Three diagrams: the overall pipeline, the NC interpreter inner loop, and the resulting object graph.

---

## 1 · Pipeline

```mermaid
flowchart TD
    ENTRY(["LoadNCCodeFile(filename.lst)\nCAMLib/NCCodeFile.cs"])

    subgraph PLST["ProcessLST()"]
        direction TB

        subgraph S1["Stage 1 — Structural parse   NcConfig.Create()"]
            direction LR
            LSTITR["LstItr\nhandles * / - line\ncontinuations"]
            TABLES["BEGIN_…ENDE_ blocks\n→ LstTable list\nEINRICHTEPLAN_INFO\nSHEET_TECH · LTT_CALLS\nFERTIGUNG_AUFTRAG_TMP …"]
            LISTINGS["START_TEXT…STOP_TEXT\n→ mNcListings dict\nkey = program name\nvalue = List&lt;string&gt; lines"]
            LSTITR --> TABLES
            LSTITR --> LISTINGS
        end

        subgraph S2["Stage 2 — NC interpretation   NcConfig.InitOpTree()"]
            direction TB
            CTX["NcProgramContext.Execute()\nmain program"]
            XFM["NcCoordXfm\nTRANS · ATRANS\nROT · AROT"]
            SUB["NcContext.BuildSubprogram()\n↺ NcProgramContext.Execute()\nfor each called subroutine\nmax depth 8 · memoised"]
            OBB["IOpBlockBuilder\nAddLineTo  AddArcTo\nAddPunchLineTo  AddPunchArcTo\nAddOpBlockRef  Completed()"]
            CTX -- "frame commands" --> XFM
            XFM -- "transforms coords" --> OBB
            CTX -- "subroutine name literal" --> SUB
            SUB -- "HeadOpBlock per sub" --> OBB
            CTX -- "motion lines" --> OBB
        end

        subgraph S3["Stage 3 — Assembly   JobUtil2.CreateJob()"]
            direction LR
            OTREE["OpTreeBuilder\nReconstructedMainBlock\n(HeadOpBlock tree)"]
            SHEET["Sheet(sheetX, sheetY, sheetZ)\nfrom SHEET_TECH cols 20 30 40"]
            PUNCHES["Punch palette\nStdPunch / CustomPunch\nfrom PROGRAMM + WZG_STAMM"]
            JOB["Job\n└─ Workitem\n   ├─ Workpiece (Sheet)\n   └─ HeadOpBlock"]
            OTREE --> JOB
            SHEET --> JOB
            PUNCHES --> JOB
        end

        subgraph S4["Stage 4 — Part geometry   LoadParts()"]
            direction LR
            WALK["Walk HeadOpBlockRef nodes\nin HeadOpBlock.HeadOps"]
            CSP["ConsumeSubPgm()\nHeadOpUnit → Pline.Add(pt)\narc → Pline.Add(pt, center, ccw)\nmove → flush Pline, start new"]
            PARTS["LSTPart[]\nname + List&lt;Pline&gt; + SubPgms"]
            WALK --> CSP --> PARTS
        end

        subgraph S5["Stage 5 — DIN generation   ProcessPgm()"]
            direction TB
            HDR["DIN header\n%1(name) · MACHINE · USER\nMachiningTime · Sheet_Size_X/Y\nG90/G91 · M15 · G17 · CheckBoundPrg"]
            MAP["LST → DIN command mapping\nTC_LASER_ON → M1014 + TC_UPDATE_PARAM\nTC_LASER_OFF → M1015\nG823 → comment  G824 → M1030/31\nROT → G89 C=  · sub call → G22 Jn"]
            DIN["Write  {ProgramsPath}/DINs/name.din"]
            HDR --> DIN
            MAP --> DIN
        end
    end

    subgraph DINPARSE["DINFile — CAMLib/DINFile.cs"]
        DF["new DINFile(dinPath)\nparse DIN syntax\nbuild Contours[]  Drawing\nLines[]  SheetSize  Name"]
    end

    RESULT(["NCCodeFile returned to caller"])

    ENTRY --> LSTITR
    LISTINGS --> CTX
    S1 --> S2
    S2 --> S3
    S3 --> S4
    S3 --> S5
    S5 --> DF
    DF --> RESULT
```

---

## 2 · NcProgramContext.Execute() — per-line interpreter

```mermaid
flowchart TD
    START(["Execute() — begin"])
    INIT["Init state\nheadX/Y = 0,0\nisLaserOn = false\nxfm = NcCoordXfm.Identity\nmUseAbsoluteDimension = inherited\n\nMain program only:\nxfm = xfm.Translated(-zeroX, -zeroY)"]
    NEXTLINE{"next line\nfrom TextLineItr"}
    DONE(["Completed(totalLines)\n→ HeadOpBlock added to OpTreeBuilder"])

    PARSE["NcItem.Parse(line)\n→ token stream"]

    NCWORD["NcWord token\nX Y I J G A B Z F M"]
    COMMENT["Comment token\n;ENTRY_LASER:\n;ENTRY_PUNCH:"]
    LITERAL["Literal token"]
    SUBCALL["SubCall token\nTC_LASER_ON/OFF\nTC_TOOL_LOAD\nTC_SHEET_REPOSIT"]
    ROT["NcROT / NcAROT token\nROT RPL=  /  AROT RPL="]

    EXTRACT["Extract into registers\nncX  ncY  ncI  ncJ  ncG\nscale by mUnitScale\nset mBits flags\nG70→scale=25.4  G71→scale=1\nG90/G91 → abs/inc mode"]

    PARTBEGINS["partBegins = true\nSetPartEntryLine(lineNum)"]

    TRANS["TRANS / ATRANS\nxfm = xfm.Translated(x,y,additive)"]
    ISSUBPGM{"IsSubProgramName\n(literal)?"}
    UNKNOWN["skip line"]
    ROTXFM["xfm = xfm.Rotated(angle, additive)"]

    LASER["isLaserOn = true/false\nSetHeadActiveLine / SetHeadInactiveLine"]
    TOOL["PunchId = parameter"]
    REPOS["xfm = xfm.Translated(-repoX,-repoY, additive)"]

    TRANSFORM{"has X or Y?"}

    ABSMODE["Absolute:\nx,y = ncX,ncY\nxfm.TransformPointXY(ref x, ref y)\nheadX = x  headY = y"]
    INCMODE["Incremental:\ndx = ncX  dy = ncY\nxfm.TransformVectorXY(ref dx, ref dy)\nheadX += dx  headY += dy"]

    MOTION{"G02/G03\nwith I or J?"}

    ARCIJ["Arc center:\nradX = ncI  radY = ncJ\nxfm.TransformVectorXY()\ncenterX = lastX + radX\ncenterY = lastY + radY\n\nfull circle if no X/Y or\ndisplacement == 0"]

    PUNCHACTIVE{"punch/nibble\nactive?"}

    ADDARC["AddArcTo(\nlineNum, toX, toY,\ncenterX, centerY, bits)"]
    ADDPUNCHARC["AddPunchArcTo(\nlineNum, toX, toY,\ncenterX, centerY,\npunchId, …, bits)"]
    ADDLINE["AddLineTo(\nlineNum, toX, toY, bits)"]
    ADDPUNCHLINE["AddPunchLineTo(\nlineNum, toX, toY,\npunchId, …, bits)"]

    BUILDSUB["NcContext.BuildSubprogram(this, name)\n→ new NcProgramContext\n→ Execute() ↺\n→ returns dispX, dispY, xfmDelta\nAddOpBlockRef(lineNum, name, headX, headY, …)"]

    START --> INIT --> NEXTLINE
    NEXTLINE -->|"line"| PARSE
    NEXTLINE -->|"EOF"| DONE

    PARSE --> NCWORD & COMMENT & LITERAL & SUBCALL & ROT

    NCWORD --> EXTRACT --> TRANSFORM
    COMMENT --> PARTBEGINS --> NEXTLINE
    LITERAL -->|"TRANS/ATRANS"| TRANS --> NEXTLINE
    LITERAL --> ISSUBPGM
    ISSUBPGM -->|yes| BUILDSUB --> NEXTLINE
    ISSUBPGM -->|no| UNKNOWN --> NEXTLINE
    ROT --> ROTXFM --> NEXTLINE
    SUBCALL -->|"TC_LASER_ON/OFF"| LASER --> NEXTLINE
    SUBCALL -->|"TC_TOOL_LOAD"| TOOL --> NEXTLINE
    SUBCALL -->|"TC_SHEET_REPOSIT"| REPOS --> NEXTLINE

    TRANSFORM -->|"no X/Y"| NEXTLINE
    TRANSFORM -->|yes, absolute| ABSMODE --> MOTION
    TRANSFORM -->|yes, incremental| INCMODE --> MOTION

    MOTION -->|yes| ARCIJ --> PUNCHACTIVE
    MOTION -->|no| PUNCHACTIVE

    PUNCHACTIVE -->|no, arc| ADDARC --> NEXTLINE
    PUNCHACTIVE -->|yes, arc| ADDPUNCHARC --> NEXTLINE
    PUNCHACTIVE -->|no, line| ADDLINE --> NEXTLINE
    PUNCHACTIVE -->|yes, line| ADDPUNCHLINE --> NEXTLINE
```

---

## 3 · Result object graph

```mermaid
classDiagram
    direction TB

    class NCCodeFile {
        <<abstract>>
        +string Name
        +string FilePath
        +Size SheetSize
        +LoadNCCodeFile(filename)$ NCCodeFile
        +CheckForRemnant()
    }

    class DINFile {
        +NCContour[] Contours
        +NCDrawing Drawing
        +string[] Lines
        +bool ContainsNJ
    }

    class Job {
        +Workitem Workitem
        -double mZeroOffsetX
        -List~IPunch~ mPunches
        +SetZeroOffset(x)
        +AddPunch(punch)
        +ComputeAbsLineNumber(callstack)
        +GetOpPath(callstack)
    }

    class Workitem {
        +Workpiece Workpiece
        +HeadOpBlock HeadOpBlock
    }

    class Workpiece {
        <<abstract>>
        +double Width
        +double Height
        +double Thickness
        +Types Type
    }

    class Sheet {
        +Types.Sheet
    }

    class Tube {
        +Types.Tube
    }

    class HeadOpBlock {
        +string Name
        +string NameLower
        +List~HeadOp~ HeadOps
        +int TotalLines
        +List~int~ PartEntryLines
        +List~int~ HeadOnLines
        +List~int~ HeadOffLines
        +int ColorZLength
        +int LinesDeepcount
    }

    class HeadOp {
        <<abstract>>
        +int LineNumber
        +int AbsLineNumber
        +int ColorZIndex
        +bool IsUnitOp
    }

    class HeadOpUnit {
        +Point ToPt
        +Point ArcCenter
        +bool IsMoveOp
        +bool IsArc
        +bool IsCcw
        +bool IsCircle
        +bool IsBeamOff
        +bool IsEntryLaserHinted
        +bool IsLaserOn
        +CreateLinearToOp(...)$
        +CreateArcToOp(...)$
    }

    class PunchOp {
        +string PunchId
        +float PunchAngle
        +int PunchCount
        +CreateLineHitOp(...)$
        +CreateArcHitOp(...)$
    }

    class HeadOpBlockRef {
        +HeadOpBlock Block
        +Point Pos
        +double Rotation
        +NcCoordXfm Xfm
        +List~string~ Labels
        +bool BeginsPart
    }

    class NcCoordXfm {
        -double mOriginX
        -double mOriginY
        -double mRotation
        -double mSin
        -double mCos
        +double FrameRotation
        +bool HasTranslation
        +bool IsIdentity
        +Identity$ NcCoordXfm
        +TransformPointXY(ref x, ref y)
        +TransformVectorXY(ref x, ref y)
        +Translated(dx, dy, additive) NcCoordXfm
        +Rotated(angle, additive) NcCoordXfm
        +Appended(other) NcCoordXfm
        +Inverted() NcCoordXfm
    }

    class LstTable {
        +string Name
        +int StartLineNumber
        -List~ColumnInfo~ mColumns
        -List~string[]~ mRowCells
        +GetS(row, colId) string
        +GetF(row, colId) double
        +GetMM(row, colId) double
        +Rows IEnumerable
    }

    class NcConfig {
        +string MainProgramName
        +double SheetX
        +double SheetY
        +double SheetZ
        +double ZeroPointX
        +double ZeroPointY
        +bool UsesInch
        +bool IsMainPrgAbsolute
        +bool MeasureSheetPosition
        +Create(lstContent)$ NcConfig
        +InitOpTree(builder, logger)
        +GetTables() List~LstTable~
        +GetNcPrgListings()
        +GetPunchIds()
    }

    class LSTPart {
        +string Name
        +List~Pline~ mPlines
        +List~string~ SubPgms
        +Add(pline)
    }

    class Pline {
        +Point2 P1
        +Point2 P2
        +List~Seg2~ Segs
        +bool IsClosed
        +bool IsCircle
        +int Count
        +Add(pt)
        +Add(pt, center, ccw)
    }

    NCCodeFile <|-- DINFile
    Job "1" *-- "1" Workitem
    Workitem "1" *-- "1" Workpiece
    Workpiece <|-- Sheet
    Workpiece <|-- Tube
    Workitem "1" *-- "1" HeadOpBlock
    HeadOpBlock "1" *-- "0..*" HeadOp
    HeadOp <|-- HeadOpUnit
    HeadOpUnit <|-- PunchOp
    HeadOp <|-- HeadOpBlockRef
    HeadOpBlockRef "1" --> "1" HeadOpBlock : references
    HeadOpBlockRef "1" *-- "1" NcCoordXfm
    NcConfig "1" *-- "0..*" LstTable
    LSTPart "1" *-- "0..*" Pline
    Job ..> NcConfig : created from
    Job ..> LSTPart : LoadParts produces
    DINFile ..> Job : ProcessLST consumes
```

---

## 4 · Data flow summary

```mermaid
flowchart LR
    LST(["filename.lst\nraw text"])

    subgraph LSTPARSER["LSTParser.dll"]
        NC["NcConfig\ntables + listings"]
        TREE["HeadOpBlock tree\nHeadOpUnit · PunchOp\nHeadOpBlockRef"]
        JOB["Job\nWorkitem · Sheet\nPunch palette"]
    end

    subgraph CAMLIB["CAMLib.dll"]
        PARTS["LSTPart[]\nPline geometry"]
        DIN["name.din\non disk"]
        DINFILE["DINFile\nNCCodeFile"]
    end

    LST -->|"NcConfig.Create()"| NC
    NC -->|"InitOpTree()\nNcProgramContext.Execute()"| TREE
    TREE -->|"JobUtil2.CreateJob()"| JOB
    JOB -->|"LoadParts()"| PARTS
    JOB -->|"ProcessPgm()\nLST→DIN mapping"| DIN
    DIN -->|"new DINFile()"| DINFILE
```
