# Diagnosing freezes and crashes

## The game freezes: `hangsnap`

Use this when the game stops responding: the picture stops, sound may loop or stop, and Windows marks the window "Not Responding", but the process doesn't exit. Leave the game frozen and run, from this repository:

```bash
python -m mercfoundry hangsnap
```

It finds the running `mercenaries_recomp.exe` (`--pid N` if there are several), pauses each of its threads just long enough to read its stack, then lets them carry on. It changes nothing in the game. It prints:

1. **Guest registers and current function**: the emulated Xbox CPU's `eax`..`edi` and `esp`, and the guest function the port last entered (`g_recomp_current_func`).
2. **Every native thread's stack**, innermost frame first. Frames in the port are named from its linker map: the recompiled game is `sub_XXXXXXXX` (named after the function's Xbox address), followed by the name from [map/functions.json](../map/functions.json) where we know one and the XBE section when it isn't the game's own `.text` (`D3D`, `DSOUND`, `XACTENG`, `XMV`... are Xbox libraries linked into the game). The port's own code (`pgraph_method`, `nv2a_mmio_write`, `d3d8_PresentFrame`...) is shown by its C name, and Windows and driver DLLs as `name.dll+offset`. Threads with identical stacks are folded together (the graphics driver alone runs dozens of identical workers), and threads running the port's code come first.
3. **Recently entered guest functions**: the port's ring buffers of the last 64 functions entered and the last 256 game (non-library) functions, oldest first. They only fill while the port's entry tracing is on; the report says when it's off.
4. **The guest stack**: words at the guest `esp` that point into guest code, each with the recompiled function it falls in. Most are return addresses, so they show the guest call chain, but a stack also holds data that happens to look like a code address; treat this as a hint.

### Reading it

Start with the thread that runs the recompiled game: it is the one with `sub_XXXXXXXX` frames, ending in `xbe_entry_point` and `WinMain`. Read its stack from the bottom up (oldest call first) to see what the game was doing, and from the top to see where it stopped.

**The thread that is waiting is not necessarily the culprit.** A frozen thread is usually blocked in a wait (`ntdll.dll`/`KERNELBASE.dll` frames on top) for something another part of the program was supposed to do. For example, the game's D3D library (`sub_0028E650` and its neighbours, section `D3D`) waits for the emulated GPU to catch up with the command buffer; if the emulated GPU stopped on bad data, the game thread shows up waiting in D3D, called from whatever was drawing, even though the bad data was written earlier, perhaps by a mod's edited files. So:

- Look at what the waiting thread is waiting *for*, and at the other threads with the port's code on them (the GPU, audio and kernel threads).
- Take two or three snapshots a few seconds apart. A stack that never changes is stuck in place; one that moves is a loop.
- Note the game functions above the library frames (what was being drawn or loaded), then narrow it down by switching mods or mod files off and on until the hang comes and goes.

### Out of memory

When the game runs out of memory it doesn't crash: it retries the allocation for good (`Heap_AllocRetrying`, `0x1F70F0`), running its out-of-memory handlers between tries, and Windows soon marks it "Not Responding". `hangsnap` says so at the top of its report ("what it looks like"). Two signs of it:

- The game thread's stack changes between snapshots but always passes through `sub_001F70F0`; with `sub_00222190` (`StreamQueue_Issue`) under it, the memory was for streaming in the world around the player: models, textures and terrain.
- One handler writes an "Actor Memory Usage" report to the Xbox's `z:\PoiDump.htm` on every try: in the port, `mod-cache/<hash>/storage/Cache/PoiDump.htm`, rewritten every few seconds. Its totals cover the placed objects only (each template's size and count), so small totals there point at the streamed data.

Seen on the Gaza mod's map (2026-11-18): placed objects used 263 KB, and streaming couldn't get a buffer. A mod hits this by loading more models or textures in one area than the game has room for; make them smaller or load fewer at a time.

Seen again there the same day, after the mod repainted a lorry load at 1024x1024: the stack held a 702 KB request, the 699 KB texture rounded up to whole sectors, while the main heap had 1.07 MB free in 42 pieces, the largest 637 KB. Each streamed texture passes through the main heap in one piece, so one big texture needs one big free gap even when the total free is enough. `mod build` warns about a texture larger than retail's 512 KB textures (retail has one bigger one, 699 KB). To find the size, read the main heap (`0x645984`: free-block array at `+0x10`, count at `+0x18`, 8 bytes each, size in the second word) and look for sizes on the game thread's stack.

`hangsnap` now does that for you while the game is still running: after the stacks it prints the main heap's free total, its piece count and the largest piece. It also prints the allocation the game is waiting on, with its size and the source line the game tags it with. Examples are `RedModel.cpp [1492]`, a model's geometry, and `[1496]`, the same geometry packed.

Seen a third time there (2026-11-18): the streamer (`0x1F78D0` -> `0x1F63B0` reading a model -> `0x1F59B0` reading its `CSEG`) wanted 158,176 bytes for the model gaza_block_k's geometry. The heap had 317 KB free in 83 pieces, the largest 137,920 bytes. The game unpacks each model's geometry into one block as it streams in. Retail keeps buildings under about 138 KB; only vehicles and the C-17 go above. Gaza had 84 models over 128 KB, up to 304 KB. `mod build` now warns about any model whose geometry unpacks to more than 128 KB.

### Options

- `--save FILE` keeps the raw snapshot; `--load FILE --port-map <mercenaries_recomp.map>` reads it again later (for example after `map/functions.json` gains names).
- `--port-map` points at the linker map if it can't be found. The launcher runs a copy of the exe from `mod-cache/<hash>/runtime/`, so the map is looked for next to the running exe and in the folders above it, and the one whose timestamp matches the running build is used. With a map from another build the names are wrong, and you are warned.
- `--xbe` points at `default.xbe` for the section names (by default `game_files/default.xbe` here, or `game_files/` near the port).
- `--recent N` shows more or fewer recent game functions.

Nothing is hard-coded to one build: the addresses of the port's globals come from the linker map each time.

### The helper program

The stack walking is done by a small C program, [tools/hangsnap/hangsnap.c](../tools/hangsnap/hangsnap.c), using Windows' own dbghelp (no debugger needs to be installed). The first `hangsnap` run builds it into `tools/hangsnap/out/` with [tools/hangsnap/build.cmd](../tools/hangsnap/build.cmd), which needs Build Tools for Visual Studio 2022 (the same as for plugins). The script sets `INCLUDE`, `LIB` and `PATH` itself rather than relying on `vcvars64.bat`. Run it by hand to rebuild:

```bash
tools\hangsnap\build.cmd
```

With Smart App Control on, Windows decides whether to run an unsigned program it hasn't seen before by its contents, and may refuse a new build ("An Application Control policy has blocked this file"). A build that calls `GetProcAddress` was blocked when we tried it, and the current one runs; keep the program plain.

You can also run it directly: `hangsnap.exe <pid> <globals_rva> <globals_size> <esp_offset>` (hex), where `globals_rva` is `g_xbox_mem_offset`'s RVA in the linker map (its address minus the preferred load address) and `esp_offset` is `g_esp`'s offset from it. The output is plain text: `exe+RVA` frames that you can look up in the map.

## The game crashes: `[CRASH]` reports

When the port catches a crash (an access violation, a guest breakpoint, a divide by zero) it prints a `[CRASH]` report to stderr with the faulting address (`RIP`, module RVA) and the guest state. The launcher throws stderr away, so to keep the report, start the game from a terminal yourself, with the same command line the launcher uses: the runtime exe in `mod-cache/<hash>/runtime/` (its path is shown at the top of `hangsnap`'s output) and the retail game folder as its argument (`<port>` is the folder holding `Mercenaries Recompiled.exe`):

```bash
cd "<port>/mod-cache/<hash>/runtime"
./mercenaries_recomp.exe "<port>/game_files/mercenaries-retail" 2> crash.err.txt
```

Look up the `RIP`'s module RVA in `mercenaries_recomp.map` the same way as a `hangsnap` frame (the function whose address is the largest one below it). As with a freeze, the function that crashed may only be reading bad data written earlier.
