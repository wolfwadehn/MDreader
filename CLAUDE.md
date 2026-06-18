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
