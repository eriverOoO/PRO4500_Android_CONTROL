# PCB 3D Scanning Software Plan

## Current State

- `PRO4500.cpp` can show image files from a folder as fullscreen patterns on a selected display.
- USB control is currently limited to LightCrafter 4500 blue LED current.
- There is no scan-session layer yet: no camera trigger, no frame naming, no metadata, and no multi-exposure capture loop.

## Phase 1 Decision: Pattern Sequence

Use this sequence for the first working scan pipeline:

1. 10 Gray-code patterns, MSB to LSB.
2. 4 phase-shifting profilometry (PSP) sine patterns at 0, 90, 180, and 270 degrees.
3. Vertical fringes by default, meaning intensity changes along projector X. This is the usual first choice when the projector is offset beside the camera and height mainly shifts the observed projector X coordinate.

This exactly matches the 14-image set in the manual:

```text
10 Gray Code + 4-step PSP = 14 projected patterns per exposure set
```

For HDR capture, run the same 14 patterns for each exposure set:

```text
10 ms exposure -> 14 frames
30 ms exposure -> 14 frames
80 ms exposure -> 14 frames
Total: 42 raw frames per angle
```

The default generator uses 912 x 1140 because that is the LightCrafter 4500 native DMD resolution. If Windows exposes the projector as a different display resolution, generate patterns at that display resolution to avoid scaling artifacts.

## Phase 2: Synchronous Capture Loop

For the first implementation, keep timing simple and explicit:

```text
show pattern
sleep(settle_ms)
flush stale camera frames
capture frame
save frame + metadata
repeat
```

This is not hardware-locked synchronization, but it is enough to validate:

- pattern order
- image brightness
- camera focus
- cross-polarization setting
- basic Gray/PSP decoding feasibility

The `tools/run_scan_sequence.py` script supports webcam indexes such as `0` and smartphone/IP camera URLs such as `http://.../video`, as long as OpenCV can open them.

Install the capture dependency in the Python environment you will use:

```powershell
python -m pip install -r tools\requirements.txt
```

Important camera note:

- Webcam exposure control through OpenCV is backend-dependent.
- Smartphone cameras usually do not expose shutter control through a plain video URL.
- For reliable HDR sets, use a camera app/driver/API that can lock manual exposure, then confirm the saved frames actually differ in brightness.

## Phase 3: 0/180 Degree Rotation

Software-wise, the first version should treat angles as separate capture blocks:

```text
angle_000/
  exposure_010ms/
  exposure_030ms/
  exposure_080ms/
angle_180/
  exposure_010ms/
  exposure_030ms/
  exposure_080ms/
```

The script can pause between angles so the rotation disk can be moved manually. Later, a serial motor controller can replace that pause.

## Future: USL / Asynchronous Capture

USL should be added only after the synchronous pipeline produces usable Gray/PSP data. The future async design should record:

- projector pattern schedule with monotonic timestamps
- camera frame timestamps
- exposure time and gain per frame
- detected pattern ID, either from image content or a small corner marker

Correction strategy:

1. Decode or infer which projected pattern each captured frame belongs to.
2. Estimate camera latency and frame cadence from timestamp differences.
3. Reject transition frames captured while patterns were changing.
4. Rebuild a clean 14-pattern set per exposure from the accepted frames.

This turns async capture into a software alignment problem instead of assuming each camera frame is already synchronized.
