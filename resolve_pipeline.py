"""
Resolve Pipeline — Main Orchestrator
--------------------------------------
Steps 1–7 of the editing pipeline:

  1. Launch DaVinci Resolve
  2. Scan session folder for CAM1, CAM2, CAM3, and Audio/ZOOM#### subfolders
  3. Validate: number of ZOOM subfolders must match video count in ALL CAM folders
  4. For each TrLR wav → run silence detection → generate CSV
  5. For each CSV → generate XML (silence + voice regions)
  6. Import all tracks + XMLs into Resolve (one timeline per source type)
  7. Ding when done

Expected folder structure:
    session/
      CAM1/         ← video files
      CAM2/
      CAM3/
      Audio/
        ZOOM0017/   ← ZOOM0017_TrLR.wav, ZOOM0017_Tr1.wav, ZOOM0017_Tr2.wav
        ZOOM0018/
        ...

Usage:
    python resolve_pipeline.py --session /path/to/session/folder

Optional args:
    --silence_thresh   RMS threshold (default: 0.01)
    --min_silence_ms   Min silence duration ms (default: 300)
    --min_voice_ms     Min voice duration ms (default: 1000)
    --padding_ms       Padding around silence ms (default: 100)
    --fps              Timeline framerate (default: 29.97)
    --resolve_app      Path to DaVinci Resolve app (default: auto-detect on Mac)

Requirements:
    pip install scipy numpy
    DaVinci Resolve must be installed with scripting enabled.
"""

import argparse
import os
import sys
import time
import subprocess

# ── Paths to sibling scripts ───────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SILENCE_SCRIPT = os.path.join(SCRIPT_DIR, "silence_detection_mvp.py")
XML_SCRIPT = os.path.join(SCRIPT_DIR, "csv_to_resolve_xml.py")

# ── DaVinci Resolve scripting API path (Mac default) ──────────────────────────
RESOLVE_API_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_APP_PATH = "/Applications/DaVinci Resolve/DaVinci Resolve.app"

CAM_FOLDERS = ["Cam 1", "Cam 2", "Cam 3"]
AUDIO_FOLDER = "Audio"
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mxf")
AUDIO_EXTENSIONS = (".wav",)


# ── Step 1: Launch DaVinci Resolve ────────────────────────────────────────────
def launch_resolve(app_path=RESOLVE_APP_PATH):
    print("\n[1/7] Launching DaVinci Resolve...")
    if not os.path.exists(app_path):
        print(f"  ⚠️  Resolve not found at: {app_path}")
        print("      Set --resolve_app to the correct path.")
        sys.exit(1)

    # Check if already running
    result = subprocess.run(["pgrep", "-x", "DaVinci Resolve"], capture_output=True, text=True)
    if result.returncode == 0:
        print("  ✅ DaVinci Resolve already running.")
    else:
        subprocess.Popen(["open", app_path])
        print("  ⏳ Waiting for Resolve to start (15s)...")
        time.sleep(15)
        print("  ✅ Resolve launched.")


# ── Step 2: Scan session folder ───────────────────────────────────────────────
def scan_session(session_path):
    print(f"\n[2/7] Scanning session folder: {session_path}")

    # ── Scan CAM folders ──
    cam_files = {}
    cam_counts = {}
    for cam in CAM_FOLDERS:
        full_path = os.path.join(session_path, cam)
        if os.path.isdir(full_path):
            files = sorted([
                os.path.join(full_path, f) for f in os.listdir(full_path)
                if f.lower().endswith(VIDEO_EXTENSIONS)
            ], key=lambda f: os.stat(f).st_birthtime)
            cam_files[cam] = files
            cam_counts[cam] = len(files)
            print(f"  ✅ {cam}: {len(files)} video file(s)")
        else:
            cam_files[cam] = []
            cam_counts[cam] = 0
            print(f"  ⚠️  {cam}: folder not found")

    # ── Scan Audio/ZOOM subfolders ──
    audio_path = os.path.join(session_path, AUDIO_FOLDER)
    if not os.path.isdir(audio_path):
        print(f"\n  ❌ Audio folder not found at: {audio_path}")
        print("     Hey Liam — please check the session folder structure.")
        sys.exit(1)

    zoom_folders = sorted([
        d for d in os.listdir(audio_path)
        if os.path.isdir(os.path.join(audio_path, d)) and d[-4:].isdigit()
    ])

    if not zoom_folders:
        print(f"\n  ❌ No ZOOM#### subfolders found in Audio/")
        print("     Hey Liam — please check the Audio folder.")
        sys.exit(1)

    print(f"\n  Audio/: {len(zoom_folders)} ZOOM folder(s) found: {', '.join(zoom_folders)}")

    # ── Validate counts match across all CAM folders ──
    unique_counts = set(cam_counts.values())
    if len(unique_counts) > 1:
        print(f"\n  ❌ CAM folders have mismatched file counts: { {k: v for k, v in cam_counts.items()} }")
        print("     Hey Liam — please clean the CAM folders so each has the same number of files.")
        sys.exit(1)

    cam_count = list(unique_counts)[0]
    if cam_count == 0:
        print("\n  ❌ No video files found in any CAM folder.")
        print("     Hey Liam — please check the CAM folders.")
        sys.exit(1)

    if len(zoom_folders) != cam_count:
        print(f"\n  ❌ Mismatch: {len(zoom_folders)} ZOOM folder(s) in Audio vs {cam_count} video(s) in each CAM folder.")
        print("     Hey Liam — the number of ZOOM takes must match the number of camera files.")
        sys.exit(1)

    print(f"  ✅ Validation passed: {cam_count} take(s) across CAM + Audio folders.")

    # ── Collect TrLR, Tr1, Tr2 files from each ZOOM folder ──
    trlr_files = []
    tr1_files = []
    tr2_files = []

    for zoom in zoom_folders:
        zoom_path = os.path.join(audio_path, zoom)
        trlr = os.path.join(zoom_path, f"{zoom}_TrLR.wav")
        tr1  = os.path.join(zoom_path, f"{zoom}_Tr1.wav")
        tr2  = os.path.join(zoom_path, f"{zoom}_Tr2.wav")

        missing = [f for f in [trlr, tr1, tr2] if not os.path.exists(f)]
        if missing:
            print(f"\n  ❌ Missing files in {zoom}/:")
            for m in missing:
                print(f"     {os.path.basename(m)}")
            print("     Hey Liam — please check the Audio folder.")
            sys.exit(1)

        trlr_files.append(trlr)
        tr1_files.append(tr1)
        tr2_files.append(tr2)
        print(f"  ✅ {zoom}: TrLR, Tr1, Tr2 found")

    return {
        "Cam 1": cam_files["Cam 1"],
        "Cam 2": cam_files["Cam 2"],
        "Cam 3": cam_files["Cam 3"],
        "TrLR": trlr_files,
        "Tr1":  tr1_files,
        "Tr2":  tr2_files,
    }


# ── Steps 3–4: Silence detection for each TrLR ───────────────────────────────
def run_silence_detection(trlr_files, session_path, silence_thresh, min_silence_ms, min_voice_ms, padding_ms):
    print(f"\n[3–4/7] Running silence detection on {len(trlr_files)} TrLR file(s)...")
    csv_files = []

    for i, wav_path in enumerate(trlr_files, 1):
        basename = os.path.splitext(os.path.basename(wav_path))[0]
        output_csv = os.path.join(session_path, f"silence_{basename}.csv")

        print(f"\n  [{i}/{len(trlr_files)}] {os.path.basename(wav_path)}")
        cmd = [
            sys.executable, SILENCE_SCRIPT,
            "--input", wav_path,
            "--silence_thresh", str(silence_thresh),
            "--min_silence_ms", str(min_silence_ms),
            "--min_voice_ms", str(min_voice_ms),
            "--padding_ms", str(padding_ms),
            "--output", output_csv,
        ]
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print(f"  ❌ Silence detection failed for {wav_path}")
            sys.exit(1)

        csv_files.append((basename, output_csv))
        print(f"  ✅ CSV: {output_csv}")

    return csv_files


# ── Step 5: Generate XML for each CSV ─────────────────────────────────────────
def run_xml_generation(csv_files, session_path, fps):
    print(f"\n[5/7] Generating XML files...")
    xml_files = []

    for basename, csv_path in csv_files:
        output_xml = os.path.join(session_path, f"chops_{basename}.xml")
        print(f"  {os.path.basename(csv_path)} → {os.path.basename(output_xml)}")
        cmd = [
            sys.executable, XML_SCRIPT,
            "--input", csv_path,
            "--fps", str(fps),
            "--output", output_xml,
        ]
        result = subprocess.run(cmd, capture_output=False)
        if result.returncode != 0:
            print(f"  ❌ XML generation failed for {csv_path}")
            sys.exit(1)

        xml_files.append((basename, output_xml))
        print(f"  ✅ XML: {output_xml}")

    return xml_files


# ── Step 6: Import into DaVinci Resolve ───────────────────────────────────────
def import_into_resolve(session_files, xml_files, fps):
    print(f"\n[6/7] Importing into DaVinci Resolve...")

    # Set up Resolve scripting API
    api_path = os.path.join(RESOLVE_API_PATH, "Modules")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        print(f"  ❌ Could not import DaVinciResolveScript.")
        print(f"     Make sure Resolve is running and scripting is enabled.")
        print(f"     API path: {api_path}")
        sys.exit(1)

    resolve = dvr_script.scriptapp("Resolve")
    if not resolve:
        print("  ❌ Could not connect to DaVinci Resolve.")
        sys.exit(1)

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        # Create a new project named after the session folder
        project = pm.CreateProject("Session")
        print("  📁 Created new Resolve project: Session")
    else:
        print(f"  📁 Using existing project: {project.GetName()}")

    media_pool = project.GetMediaPool()
    root_bin = media_pool.GetRootFolder()

    def get_or_create_bin(name):
        for subfolder in root_bin.GetSubFolderList():
            if subfolder.GetName() == name:
                return subfolder
        return media_pool.AddSubFolder(root_bin, name)

    def create_timeline_from_files(timeline_name, files):
        if not files:
            print(f"  ⚠️  Skipping {timeline_name} — no files.")
            return None

        bin_folder = get_or_create_bin(timeline_name)
        media_pool.SetCurrentFolder(bin_folder)

        # Import media
        clips = media_pool.ImportMedia(files)
        if not clips:
            print(f"  ⚠️  No clips imported for {timeline_name}")
            return None

        # Create timeline
        timeline = media_pool.CreateTimelineFromClips(timeline_name, clips)
        if timeline:
            print(f"  ✅ Timeline created: {timeline_name} ({len(clips)} clip(s))")
        else:
            print(f"  ⚠️  Could not create timeline: {timeline_name}")
        return timeline

    # Create one timeline per source folder
    for folder_name, files in session_files.items():
        if files:
            create_timeline_from_files(folder_name, files)

    # Create combined XML Chops timeline
    xml_paths = [xml_path for _, xml_path in xml_files]
    if xml_paths:
        xml_bin = get_or_create_bin("XML Chops")
        media_pool.SetCurrentFolder(xml_bin)
        for xml_path in xml_paths:
            basename = os.path.splitext(os.path.basename(xml_path))[0]
            imported = media_pool.ImportTimelineFromFile(xml_path)
            if imported:
                print(f"  ✅ XML timeline imported: {basename}")
            else:
                print(f"  ⚠️  Failed to import XML: {xml_path}")

    print("\n  ✅ All imports complete.")


# ── Step 7: Ding ──────────────────────────────────────────────────────────────
def ding():
    print("\n[7/7] 🔔 All done!")
    # macOS system ding
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"])


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Resolve Pipeline — Steps 1–7")
    parser.add_argument("--session", required=True, help="Path to session folder containing CAM1, TrLR, etc.")
    parser.add_argument("--silence_thresh", type=float, default=0.01)
    parser.add_argument("--min_silence_ms", type=int, default=300)
    parser.add_argument("--min_voice_ms", type=int, default=1000)
    parser.add_argument("--padding_ms", type=int, default=100)
    parser.add_argument("--fps", type=float, default=29.97)
    parser.add_argument("--resolve_app", default=RESOLVE_APP_PATH)
    args = parser.parse_args()

    if not os.path.isdir(args.session):
        print(f"Error: Session folder not found — {args.session}")
        sys.exit(1)

    # Step 1
    launch_resolve(args.resolve_app)

    # Step 2
    session_files = scan_session(args.session)

    # Steps 3–4
    csv_files = run_silence_detection(
        trlr_files=session_files["TrLR"],
        session_path=args.session,
        silence_thresh=args.silence_thresh,
        min_silence_ms=args.min_silence_ms,
        min_voice_ms=args.min_voice_ms,
        padding_ms=args.padding_ms,
    )

    # Step 5
    xml_files = run_xml_generation(csv_files, args.session, args.fps)

    # Step 6
    import_into_resolve(session_files, xml_files, args.fps)

    # Step 7
    ding()


if __name__ == "__main__":
    main()