"""
Silence Detection MVP v3
------------------------
Analyzes a TRLR .wav file and outputs ALL regions (Voice and Silence) with a Type column.
Short sounds shorter than --min_voice_ms are labeled as Silence even if above threshold.

No cuts are made — output is a CSV and printed log for editor review.

Requirements:
    pip install scipy numpy

Usage:
    python silence_detection_mvp.py --input ZOOM0018_TrLR.wav

Optional args:
    --silence_thresh   RMS amplitude threshold 0.0-1.0 (default: 0.01)
                       Below this = silence. Start here, tune up if too sensitive.
    --min_silence_ms   Minimum ms to count as a silence region (default: 300)
    --min_voice_ms     Minimum ms for a sound to count as real voice (default: 1000)
                       Shorter sounds get labeled Silence regardless of volume.
    --padding_ms       Padding in ms around silence regions (default: 100)
    --output           Output CSV filename (default: silence_regions.csv)
"""

import argparse
import csv
import os
import numpy as np
from scipy.io import wavfile


def format_timestamp(ms):
    total_seconds = ms / 1000
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"


def detect_silence_regions(
    input_path,
    silence_thresh=0.002,
    min_silence_ms=300,
    min_voice_ms=1000,
    padding_ms=0,
    output_csv="silence_regions.csv",
):
    print(f"\n--- Silence Detection MVP v3 ---")
    print(f"File          : {input_path}")
    print(f"Threshold     : {silence_thresh} RMS amplitude")
    print(f"Min silence   : {min_silence_ms} ms")
    print(f"Min voice     : {min_voice_ms} ms (shorter = treated as Silence)")
    print(f"Padding       : {padding_ms} ms")
    print(f"--------------------------------\n")

    print("Loading audio...")
    sample_rate, data = wavfile.read(input_path)

    # Normalize to float -1.0 to 1.0
    if data.dtype == np.int16:
        data = data / 32768.0
    elif data.dtype == np.int32:
        data = data / 2147483648.0
    elif data.dtype == np.uint8:
        data = (data - 128) / 128.0

    # Mix stereo to mono
    if data.ndim == 2:
        data = data.mean(axis=1)

    duration_ms = int(len(data) / sample_rate * 1000)
    print(f"Sample rate   : {sample_rate} Hz")
    print(f"Duration      : {format_timestamp(duration_ms)} ({duration_ms} ms)\n")

    # Calculate RMS in 10ms chunks
    chunk_ms = 10
    chunk_size = int(sample_rate * chunk_ms / 1000)
    num_chunks = len(data) // chunk_size

    rms_values = []
    for i in range(num_chunks):
        chunk = data[i * chunk_size:(i + 1) * chunk_size]
        rms = np.sqrt(np.mean(chunk ** 2))
        rms_values.append(rms)

    # Label each chunk
    is_silent = [rms < silence_thresh for rms in rms_values]

    # Build raw segments
    segments = []
    i = 0
    while i < len(is_silent):
        j = i
        current = is_silent[i]
        while j < len(is_silent) and is_silent[j] == current:
            j += 1
        segments.append((i * chunk_ms, j * chunk_ms, current))
        i = j

    # Absorb short voice segments into silence
    merged = []
    for start, end, silent in segments:
        duration = end - start
        if not silent and duration < min_voice_ms:
            merged.append((start, end, True))  # short sound = silence
        else:
            merged.append((start, end, silent))

    # Merge consecutive same-type segments
    consolidated = []
    for start, end, silent in merged:
        if consolidated and consolidated[-1][2] == silent:
            consolidated[-1] = (consolidated[-1][0], end, silent)
        else:
            consolidated.append([start, end, silent])

    # Apply padding to silence regions and enforce min_silence_ms
    final_regions = []
    for start, end, silent in consolidated:
        duration = end - start
        if silent:
            if duration >= min_silence_ms:
                padded_start = max(0, start - padding_ms)
                padded_end = min(duration_ms, end + padding_ms)
                final_regions.append((padded_start, padded_end, "Silence"))
            # else: too short, skip
        else:
            final_regions.append((start, end, "Voice"))

    if not final_regions:
        print("No regions detected. Try adjusting --silence_thresh or --min_voice_ms.")
        return

    # Print results
    print(f"{'#':<5} {'Type':<10} {'Start':<15} {'End':<15} {'Duration':<12} {'Start (ms)':<12} {'End (ms)'}")
    print("-" * 85)
    for i, (start, end, rtype) in enumerate(final_regions, 1):
        duration = end - start
        print(
            f"{i:<5} {rtype:<10} {format_timestamp(start):<15} {format_timestamp(end):<15} "
            f"{duration:<12} {start:<12} {end}"
        )

    # Write CSV
    with open(output_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "type", "start_ms", "end_ms", "duration_ms", "start_timecode", "end_timecode"])
        for i, (start, end, rtype) in enumerate(final_regions, 1):
            writer.writerow([i, rtype, start, end, end - start, format_timestamp(start), format_timestamp(end)])

    silence_count = sum(1 for _, _, t in final_regions if t == "Silence")
    voice_count = sum(1 for _, _, t in final_regions if t == "Voice")
    print(f"\n{len(final_regions)} total regions: {voice_count} Voice, {silence_count} Silence")
    print(f"Results saved to: {output_csv}")
    print("\nNext step: review CSV, then feed Silence timestamps into Resolve propagation script.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Detect silence and voice regions in a TRLR wav file.")
    parser.add_argument("--input", required=True, help="Path to TRLR .wav file")
    parser.add_argument("--silence_thresh", type=float, default=0.01, help="RMS threshold 0.0-1.0 (default: 0.01)")
    parser.add_argument("--min_silence_ms", type=int, default=300, help="Min silence duration ms (default: 300)")
    parser.add_argument("--min_voice_ms", type=int, default=1000, help="Min voice duration ms (default: 1000)")
    parser.add_argument("--padding_ms", type=int, default=100, help="Padding around silence ms (default: 100)")
    parser.add_argument("--output", default="silence_regions.csv", help="Output CSV (default: silence_regions.csv)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: File not found — {args.input}")
        exit(1)

    detect_silence_regions(
        input_path=args.input,
        silence_thresh=args.silence_thresh,
        min_silence_ms=args.min_silence_ms,
        min_voice_ms=args.min_voice_ms,
        padding_ms=args.padding_ms,
        output_csv=args.output,
    )