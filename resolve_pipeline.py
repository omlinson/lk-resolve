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
import logging
from datetime import datetime

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


# ── Logger ────────────────────────────────────────────────────────────────────
def setup_logger(session_path):
    log_file = os.path.join(session_path, f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout),
        ]
    )
    log = logging.getLogger("pipeline")
    log.info(f"Log file: {log_file}")
    return log


def launch_resolve(app_path=RESOLVE_APP_PATH):
    print("\n[1/7] Launching DaVinci Resolve...")
    if not os.path.exists(app_path):
        print(f"  ⚠️  Resolve not found at: {app_path}")
        print("      Set --resolve_app to the correct path.")
        sys.exit(1)

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


# ── Rename CAM files on disk before import ────────────────────────────────────
def rename_cam_files(session_files):
    print(f"\n[2b/7] Renaming CAM files on disk...")
    renamed = {}
    for cam, files in session_files.items():
        if not cam.startswith("Cam"):
            renamed[cam] = files
            continue
        new_paths = []
        for i, old_path in enumerate(files, 1):
            ext = os.path.splitext(old_path)[1]
            cam_label = cam.replace(" ", "")
            new_name = f"{cam_label}-{i}{ext}"
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            if old_path != new_path:
                if os.path.exists(new_path):
                    print(f"  ⚠️  Already exists, skipping rename: {new_name}")
                else:
                    os.rename(old_path, new_path)
                    print(f"  ✅ {os.path.basename(old_path)} → {new_name}")
            new_paths.append(new_path)
        renamed[cam] = new_paths
    return renamed


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
def import_into_resolve(session_files, xml_files, fps, session_path, log):
    log.info("[6/7] Importing into DaVinci Resolve...")

    api_path = os.path.join(RESOLVE_API_PATH, "Modules")
    log.debug(f"Resolve API path: {api_path}")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)

    try:
        import DaVinciResolveScript as dvr_script
        log.info("DaVinciResolveScript imported OK")
    except ImportError as e:
        log.error(f"Could not import DaVinciResolveScript: {e}")
        sys.exit(1)

    log.info("Connecting to Resolve...")
    resolve = dvr_script.scriptapp("Resolve")
    if not resolve:
        log.error("Could not connect to DaVinci Resolve — scriptapp returned None")
        sys.exit(1)
    log.info(f"Connected to Resolve: {resolve.GetVersionString()}")

    log.info("Waiting for Resolve to settle (5s)...")
    time.sleep(5)

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    project_name = os.path.basename(session_path.rstrip("/"))
    if not project:
        log.info(f"No current project — creating: {project_name}")
        project = pm.CreateProject(project_name)
        if not project:
            log.error(f"Failed to create project: {project_name}")
            sys.exit(1)
        log.info(f"Project created: {project_name}")
    else:
        log.info(f"Using existing project: {project.GetName()}")

    media_pool = project.GetMediaPool()
    media_storage = resolve.GetMediaStorage()
    root_bin = media_pool.GetRootFolder()

    def get_or_create_bin(name, parent=None):
        parent = parent or root_bin
        for subfolder in parent.GetSubFolderList():
            if subfolder.GetName() == name:
                return subfolder
        return media_pool.AddSubFolder(parent, name)

    cam_bins = {
        "Cam 1": get_or_create_bin("Cam 1"),
        "Cam 2": get_or_create_bin("Cam 2"),
        "Cam 3": get_or_create_bin("Cam 3"),
    }
    audio_bin = get_or_create_bin("Audio")
    audio_bins = {
        "TrLR": get_or_create_bin("TrLR", audio_bin),
        "Tr1":  get_or_create_bin("Tr1",  audio_bin),
        "Tr2":  get_or_create_bin("Tr2",  audio_bin),
    }
    bin_map = {**cam_bins, **audio_bins}

    track_order = ["Cam 1", "Cam 2", "Cam 3", "TrLR", "Tr1", "Tr2"]
    audio_tracks = {"TrLR", "Tr1", "Tr2"}

    all_clips_by_track = {}
    for track_name in track_order:
        files = session_files.get(track_name, [])
        if not files:
            log.warning(f"No files for track: {track_name}")
            all_clips_by_track[track_name] = []
            continue

        log.info(f"Importing {len(files)} file(s) for track: {track_name}")
        for f in files:
            exists = os.path.exists(f)
            log.debug(f"  {'OK' if exists else 'MISSING'} {f}")
            if not exists:
                log.error(f"File not found on disk: {f}")

        media_pool.SetCurrentFolder(bin_map[track_name])

        if track_name in audio_tracks:
            # FIX: MediaPool.ImportMedia silently rejects audio-only WAV files.
            # Use MediaStorage.AddItemListToMediaPool instead, importing one file
            # at a time to avoid issues with spaces in paths and varargs vs list.
            clips = []
            for f in files:
                log.debug(f"  Audio import (AddItemListToMediaPool): {f}")
                result = media_storage.AddItemListToMediaPool(f)
                if result:
                    clips.extend(result)
                    log.debug(f"    OK — {len(result)} clip(s)")
                else:
                    log.warning(f"    AddItemListToMediaPool returned empty, trying ImportMedia dict fallback")
                    result = media_pool.ImportMedia([{"FilePath": f}])
                    if result:
                        clips.extend(result)
                        log.debug(f"    Fallback OK — {len(result)} clip(s)")
                    else:
                        log.error(f"    Both import methods failed for: {f}")
            log.debug(f"Audio import for {track_name}: {len(clips)} total clip(s)")
        else:
            clips = media_pool.ImportMedia(files)
            log.debug(f"ImportMedia for {track_name} returned: {clips}")

        if not clips:
            log.error(f"Import returned nothing for {track_name} — check file paths and codec support")
            all_clips_by_track[track_name] = []
        else:
            log.info(f"  {len(clips)} clip(s) imported for {track_name}")
            all_clips_by_track[track_name] = clips

    # ── Create a single empty timeline ───────────────────────────────────────
    log.info(f"Creating timeline: {project_name}")

    # FIX: set frame rate before creating the timeline
    project.SetSetting("timelineFrameRate", str(fps))

    timeline = media_pool.CreateEmptyTimeline(project_name)
    if not timeline:
        log.error("Failed to create empty timeline")
        sys.exit(1)
    log.info(f"Empty timeline created: {project_name}")

    project.SetCurrentTimeline(timeline)

    # Resolve creates 1 video + 1 audio track by default.
    # We need 3 video + 4 audio, so add 2 video and 3 audio.
    # FIX: wrapped in try/except — AddTrack not supported on all Resolve versions
    try:
        for i in range(2):
            timeline.AddTrack("video")
        for i in range(3):
            timeline.AddTrack("audio")
    except Exception as e:
        log.warning(f"AddTrack failed (may not be supported on this Resolve version): {e}")
        log.warning("Continuing — clips will be placed on whatever tracks exist.")

    def get_clip_duration_frames(clip, fps):
        """Parse clip duration timecode into total frames."""
        try:
            duration = clip.GetClipProperty("Duration")
            parts = str(duration).replace(";", ":").split(":")
            h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return int((h * 3600 + m * 60 + s) * round(fps) + f)
        except Exception as e:
            log.warning(f"  Could not parse duration: {e} — defaulting to 0")
            return 0

    def place_clips_on_track(track_name, track_idx, media_type, clips):
        """Place all clips for a source sequentially on the given track."""
        if not clips:
            log.warning(f"Skipping empty track {track_idx} ({media_type}): {track_name}")
            return
        log.info(f"Placing {len(clips)} clip(s) on {media_type} track {track_idx}: {track_name}")
        record_frame = 0
        for clip in clips:
            duration_frames = get_clip_duration_frames(clip, fps)
            clip_info = {
                "mediaPoolItem": clip,
                "trackIndex": track_idx,
                "recordFrame": record_frame,
            }
            # Only set in/out points if we got a valid duration;
            # otherwise let Resolve use the full clip.
            if duration_frames > 0:
                clip_info["startFrame"] = 0
                clip_info["endFrame"] = duration_frames

            # mediaType must be int (1=video, 2=audio)
            if media_type == "video":
                clip_info["mediaType"] = 1
            elif media_type == "audio":
                clip_info["mediaType"] = 2

            result = media_pool.AppendToTimeline([clip_info])
            log.debug(f"  AppendToTimeline result: {result}")
            if duration_frames > 0:
                record_frame += duration_frames
            else:
                # Estimate from the appended timeline item if possible
                log.warning(f"  Duration unknown for clip — subsequent clips may overlap")

    # ── Video tracks: Cam 1, Cam 2, Cam 3 ────────────────────────────────────
    place_clips_on_track("Cam 1", 1, "video", all_clips_by_track.get("Cam 1", []))
    place_clips_on_track("Cam 2", 2, "video", all_clips_by_track.get("Cam 2", []))
    place_clips_on_track("Cam 3", 3, "video", all_clips_by_track.get("Cam 3", []))

    # ── Audio tracks: TrLR, Tr1, Tr2 ─────────────────────────────────────────
    place_clips_on_track("TrLR", 1, "audio", all_clips_by_track.get("TrLR", []))
    place_clips_on_track("Tr1",  2, "audio", all_clips_by_track.get("Tr1",  []))
    place_clips_on_track("Tr2",  3, "audio", all_clips_by_track.get("Tr2",  []))

    # ── XML Chops: import as timelines, not media ─────────────────────────────
    # FIX: XMLs are timelines — must use ImportTimelineFromFile, not ImportMedia
    if xml_files:
        log.info("Importing XML chops as timelines...")
        xml_bin = get_or_create_bin("XML Chops", audio_bin)
        media_pool.SetCurrentFolder(xml_bin)
        for basename, xml_path in xml_files:
            log.debug(f"  File exists: {os.path.exists(xml_path)} — {xml_path}")
            imported_timeline = media_pool.ImportTimelineFromFile(xml_path, {
                "timelineName": f"Chops - {basename}",
                "importSourceClips": False,
            })
            if imported_timeline:
                log.info(f"  ✅ XML timeline imported: {basename}")
            else:
                log.error(f"  ❌ Failed to import XML timeline: {xml_path}")

    log.info("✅ All clips placed on timeline.")


# ── Step 7: Ding + open Resolve ───────────────────────────────────────────────
def ding():
    print("\n[7/7] 🔔 All done!")
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"])
    subprocess.run(["open", "-a", "DaVinci Resolve"])


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Resolve Pipeline — Steps 1–7")
    parser.add_argument("--session", required=True, help="Path to session folder containing CAM1, TrLR, etc.")
    parser.add_argument("--silence_thresh", type=float, default=0.001)
    parser.add_argument("--min_silence_ms", type=int, default=300)
    parser.add_argument("--min_voice_ms", type=int, default=1000)
    parser.add_argument("--padding_ms", type=int, default=0)
    parser.add_argument("--fps", type=float, default=29.97)
    parser.add_argument("--resolve_app", default=RESOLVE_APP_PATH)
    args = parser.parse_args()

    if not os.path.isdir(args.session):
        print(f"Error: Session folder not found — {args.session}")
        sys.exit(1)

    log = setup_logger(args.session)
    log.info(f"Session: {args.session}")

    launch_resolve(args.resolve_app)
    session_files = scan_session(args.session)
    session_files = rename_cam_files(session_files)
    csv_files = run_silence_detection(
        trlr_files=session_files["TrLR"],
        session_path=args.session,
        silence_thresh=args.silence_thresh,
        min_silence_ms=args.min_silence_ms,
        min_voice_ms=args.min_voice_ms,
        padding_ms=args.padding_ms,
    )
    xml_files = run_xml_generation(csv_files, args.session, args.fps)
    import_into_resolve(session_files, xml_files, args.fps, args.session, log)
    ding()


if __name__ == "__main__":
    main()