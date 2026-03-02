"""
CSV to DaVinci Resolve XML
--------------------------
Reads silence_regions.csv and generates a DaVinci Resolve compatible XML
with silence regions as clips on a dedicated track.

Import the XML into DaVinci Resolve:
    File → Import Timeline → Import AAF, EDL, XML...

Requirements: no dependencies, stdlib only

Usage:
    python csv_to_resolve_xml.py

Optional args:
    --input    Input CSV file (default: silence_regions.csv)
    --fps      Timeline framerate (default: 29.97)
    --output   Output XML filename (default: silence_markers.xml)
"""

import argparse
import csv
import os
import math


def ms_to_frames(ms, fps=29.97):
    return math.floor(ms / 1000 * fps)


def ms_to_timecode(ms, fps=29.97):
    total_frames = ms_to_frames(ms, fps)
    frames = total_frames % round(fps)
    seconds = (total_frames // round(fps)) % 60
    minutes = (total_frames // round(fps) // 60) % 60
    hours = total_frames // round(fps) // 3600
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frames:02d}"


def generate_xml(input_csv, fps=29.97, output_xml="silence_markers.xml"):
    print(f"\n--- CSV to Resolve XML ---")
    print(f"Input CSV  : {input_csv}")
    print(f"Framerate  : {fps}")
    print(f"Output XML : {output_xml}\n")

    # Read CSV
    regions = []
    max_end_ms = 0
    with open(input_csv, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            end_ms = int(row["end_ms"])
            if end_ms > max_end_ms:
                max_end_ms = end_ms
            if row["type"] == "Silence":
                regions.append({
                    "start_ms": int(row["start_ms"]),
                    "end_ms": end_ms,
                    "duration_ms": int(row["duration_ms"]),
                    "index": row["index"],
                })

    if not regions:
        print("No Silence regions found in CSV.")
        return

    total_frames = ms_to_frames(max_end_ms, fps)
    sequence_tc = ms_to_timecode(0, fps)

    # Build XML
    clips_xml = ""
    for r in regions:
        start_frame = ms_to_frames(r["start_ms"], fps)
        end_frame = ms_to_frames(r["end_ms"], fps)
        clip_duration = end_frame - start_frame
        if clip_duration <= 0:
            continue

        clips_xml += f"""
            <clipitem id="silence_{r['index']}">
                <n>SILENCE_{r['index']}</n>
                <enabled>TRUE</enabled>
                <duration>{clip_duration}</duration>
                <rate>
                    <timebase>30</timebase>
                    <ntsc>TRUE</ntsc>
                </rate>
                <start>{start_frame}</start>
                <end>{end_frame}</end>
                <in>0</in>
                <out>{clip_duration}</out>
                <file id="silence_file_{r['index']}">
                    <n>SILENCE_{r['index']}</n>
                    <duration>{clip_duration}</duration>
                    <rate>
                        <timebase>30</timebase>
                        <ntsc>TRUE</ntsc>
                    </rate>
                    <media>
                        <video>
                            <duration>{clip_duration}</duration>
                        </video>
                    </media>
                </file>
            </clipitem>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="5">
    <sequence>
        <n>Silence Markers</n>
        <duration>{total_frames}</duration>
        <rate>
            <timebase>30</timebase>
            <ntsc>TRUE</ntsc>
        </rate>
        <timecode>
            <rate>
                <timebase>30</timebase>
                <ntsc>TRUE</ntsc>
            </rate>
            <string>{sequence_tc}</string>
            <frame>0</frame>
            <displayformat>DF</displayformat>
        </timecode>
        <media>
            <video>
                <format>
                    <samplecharacteristics>
                        <rate>
                            <timebase>30</timebase>
                            <ntsc>TRUE</ntsc>
                        </rate>
                        <width>1920</width>
                        <height>1080</height>
                    </samplecharacteristics>
                </format>
                <track>
                    <enabled>TRUE</enabled>
                    <locked>FALSE</locked>{clips_xml}
                </track>
            </video>
        </media>
    </sequence>
</xmeml>"""

    with open(output_xml, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"{len(regions)} silence regions written to {output_xml}")
    print(f"\nImport into DaVinci Resolve:")
    print(f"  File → Import Timeline → Import AAF, EDL, XML...")
    print(f"  Select: {output_xml}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert silence CSV to DaVinci Resolve XML.")
    parser.add_argument("--input", default="silence_regions.csv", help="Input CSV (default: silence_regions.csv)")
    parser.add_argument("--fps", type=float, default=29.97, help="Timeline framerate (default: 29.97)")
    parser.add_argument("--output", default="silence_markers.xml", help="Output XML filename (default: silence_markers.xml)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found — {args.input}")
        exit(1)

    generate_xml(
        input_csv=args.input,
        fps=args.fps,
        output_xml=args.output,
    )