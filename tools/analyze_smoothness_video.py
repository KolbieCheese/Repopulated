"""Measure recorded ship motion without confusing HUD/effects with movement.

Requires numpy and Pillow. FFmpeg decodes actual source frames with their PTS;
no conversion to constant frame rate is performed. The colour detector is tuned
for the stock blue/white test ships, not arbitrary factions or modded palettes.
Trajectory changes are observations, not proof of network jitter: camera motion,
deliberate input, overlapping ships and pixel quantisation can also cause them.
"""

from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw


def components(mask, cell=6):
    """Connected components of a coarse blue mask, avoiding optional cv2/scipy."""
    height, width = mask.shape
    height_cells, width_cells = math.ceil(height / cell), math.ceil(width / cell)
    padded = np.zeros((height_cells * cell, width_cells * cell), dtype=bool)
    padded[:height, :width] = mask
    grid = padded.reshape(height_cells, cell, width_cells, cell).any(axis=(1, 3))
    seen = set()
    for sy, sx in zip(*np.nonzero(grid)):
        if (sy, sx) in seen:
            continue
        todo, group = [(int(sy), int(sx))], []
        seen.add((sy, sx))
        while todo:
            y, x = todo.pop()
            group.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if (0 <= ny < height_cells and 0 <= nx < width_cells
                            and grid[ny, nx] and (ny, nx) not in seen):
                        seen.add((ny, nx))
                        todo.append((ny, nx))
        ys, xs = zip(*group)
        yield (max(0, min(xs) * cell - 5), max(0, min(ys) * cell - 5),
               min(width, (max(xs) + 1) * cell + 5),
               min(height, (max(ys) + 1) * cell + 5))


def detect(frame):
    rgb = frame.astype(np.int16)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    blue = (b > 105) & (g > 65) & ((b - r) > 32) & ((b - g) > 12)
    white = (r > 90) & (g > 90) & (b > 90) & ((np.maximum.reduce([r, g, b])
             - np.minimum.reduce([r, g, b])) < 50)
    candidates = []
    for x0, y0, x1, y1 in components(blue):
        if not (12 <= x1 - x0 <= 190 and 12 <= y1 - y0 <= 190):
            continue
        local_blue = blue[y0:y1, x0:x1]
        local_white = white[y0:y1, x0:x1]
        if local_blue.sum() < 35 or local_white.sum() < 20:
            continue
        # A compact ship body gives more reliable position than exhaust/glow.
        body = local_white | local_blue
        ys, xs = np.nonzero(body)
        cx, cy = float(xs.mean() + x0), float(ys.mean() + y0)
        centred = np.column_stack((xs - xs.mean(), ys - ys.mean()))
        covariance = centred.T @ centred / max(len(xs), 1)
        vals, vectors = np.linalg.eigh(covariance)
        axis = vectors[:, -1]
        angle = math.atan2(float(axis[1]), float(axis[0]))
        anisotropy = float((vals[-1] - vals[0]) / max(vals[-1], 1))
        candidates.append({"x": cx, "y": cy, "angle": angle,
                           "anisotropy": anisotropy, "area": int(body.sum()),
                           "blue": int(local_blue.sum()), "bbox": [x0, y0, x1, y1]})
    return candidates


def read_exact(stream, size):
    chunks, count = [], 0
    while count < size:
        value = stream.read(size - count)
        if not value:
            break
        chunks.append(value)
        count += len(value)
    return b"".join(chunks)


def grid_patch_geometry(crop):
    """A small off-centre flight-scene patch, clear of pilot and top-left HUD.

    Coordinates scale with the existing standard flight crop (650x350 at 960
    capture width, or its 1080p equivalent). A custom crop can invalidate these
    exclusions, which is recorded as a measurement limitation.
    """
    x, y, width, height = crop
    patch = [round(width * 160 / 650), round(height * 70 / 350),
             round(width * 140 / 650), round(height * 150 / 350)]
    if min(patch[2:]) < 60:
        raise ValueError("Grid detection needs a standard scene crop at least about 300x150")
    scale = min(1, 160 / max(patch[2:]))
    target = [max(1, round(patch[2] * scale)), max(1, round(patch[3] * scale))]
    return patch, target


def grid_candidate(patch):
    """Recognise the observed dark, continuous orthogonal grid, not darkness.

    Require at least three thin periodic lines in both axes, a shared pitch and
    bright intersections. Blank fades, stars, a single cross and broad geometry
    therefore cannot satisfy the test merely by having a black background.
    Work is bounded to a patch of at most 160x160 pixels by the caller.
    """
    rgb = patch.astype(np.float32)
    maximum, minimum = rgb.max(axis=2), rgb.min(axis=2)
    light = rgb.mean(axis=2)
    baseline = float(np.median(light))
    if baseline > 12 or float((maximum < 6).mean()) < .65:
        return None
    bright = (light > baseline + 8) & (maximum - minimum < 12)

    def lines(support):
        indices = np.flatnonzero(support >= .70)
        if not len(indices):
            return None
        groups = np.split(indices, np.flatnonzero(np.diff(indices) > 1) + 1)
        if not 3 <= len(groups) <= 12 or any(len(group) > 4 for group in groups):
            return None
        centres = np.array([np.average(group, weights=support[group]) for group in groups])
        gaps = np.diff(centres)
        pitch = float(np.median(gaps))
        tolerance = max(1.5, pitch * .06)
        if not 8 <= pitch <= 64 or float(np.max(np.abs(gaps - pitch))) > tolerance:
            return None
        return centres, pitch, float(min(support[round(value)] for value in centres))

    vertical, horizontal = lines(bright.mean(axis=0)), lines(bright.mean(axis=1))
    if vertical is None or horizontal is None:
        return None
    columns, x_pitch, vertical_support = vertical
    rows, y_pitch, horizontal_support = horizontal
    if abs(x_pitch - y_pitch) > max(1.5, min(x_pitch, y_pitch) * .06):
        return None
    row_indices = np.rint(rows).astype(int)
    column_indices = np.rint(columns).astype(int)
    intersections = float(bright[np.ix_(row_indices, column_indices)].mean())
    if intersections < .85:
        return None
    return {"vertical_lines": len(columns), "horizontal_lines": len(rows),
            "vertical_pitch_pixels": x_pitch, "horizontal_pitch_pixels": y_pitch,
            "minimum_vertical_line_support": vertical_support,
            "minimum_horizontal_line_support": horizontal_support,
            "bright_intersection_fraction": intersections,
            "patch_black_fraction": float((maximum < 6).mean())}


def grid_summary(crop, pts, candidates, candidate_flags):
    patch, target = grid_patch_geometry(crop)
    groups, run = [], []
    for index, flag in enumerate(candidate_flags):
        if flag:
            run.append(index)
        elif run:
            groups.append(run)
            run = []
    if run:
        groups.append(run)
    runs = [{"first_frame": group[0], "last_frame": group[-1], "frames": len(group),
             "first_source_pts_seconds": pts[group[0]],
             "last_source_pts_seconds": pts[group[-1]],
             "next_capture_pts_seconds": pts[group[-1] + 1] if group[-1] + 1 < len(pts) else None}
            for group in groups[:256]]
    annotated = [dict(value, source_pts_seconds=pts[value["frame"]]) for value in candidates]
    return {"analysed_frames": len(candidate_flags),
            "gridlike_frames": sum(candidate_flags), "gridlike_runs": len(groups),
            "scene_patch_full_capture_xywh": [crop[0] + patch[0], crop[1] + patch[1], *patch[2:]],
            "detector_resolution": target,
            "candidate_frames": annotated, "candidate_rows_truncated": sum(candidate_flags) > len(candidates),
            "runs": runs, "run_rows_truncated": len(groups) > len(runs),
            "limits": ["Candidates identify the observed grid pattern; they do not establish its cause.",
                       "Standard flight crop excludes the pilot/HUD; a custom crop or a legitimate grid menu can change interpretation.",
                       "Requires at least three thin periodic lines per axis; occlusion, another zoom or capture dropping a flash can hide it.",
                       "Runs describe captured source frames, not every native or compositor frame.",
                       "Detailed rows are capped at 256; scalar frame/run counts still cover the whole input."]}


def analyse_grid_only(video, ffmpeg, crop, prefix):
    """Decode only the bounded detector patch, preserving every source PTS."""
    patch, target = grid_patch_geometry(crop)
    x, y, width, height = patch
    out_width, out_height = target
    candidates, flags = [], []
    with tempfile.TemporaryFile(mode="w+b") as diagnostics:
        process = subprocess.Popen([
            str(ffmpeg), "-hide_banner", "-i", str(video), "-an", "-vf",
            f"crop={width}:{height}:{crop[0] + x}:{crop[1] + y},"
            f"scale={out_width}:{out_height}:flags=neighbor,showinfo",
            "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-threads", "2",
            "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=diagnostics)
        index = 0
        while True:
            data = read_exact(process.stdout, out_width * out_height * 3)
            if not data:
                break
            if len(data) != out_width * out_height * 3:
                raise RuntimeError("Truncated decoded detector frame")
            frame = np.frombuffer(data, dtype=np.uint8).reshape(out_height, out_width, 3)
            result = grid_candidate(frame)
            flags.append(result is not None)
            if result is not None and len(candidates) < 256:
                candidates.append(dict(result, frame=index))
            index += 1
        if process.wait() != 0:
            diagnostics.seek(0)
            raise RuntimeError(diagnostics.read().decode(errors="replace")[-4000:])
        diagnostics.seek(0)
        pts = [float(value) for value in re.findall(r"\bn:\s*\d+.*?pts_time:([\d.e+-]+)",
                                                  diagnostics.read().decode(errors="replace"))]
    if len(pts) != index or not pts:
        raise RuntimeError(f"PTS/frame mismatch: {len(pts)} / {index}")
    report = {"video": str(video.resolve()), "source_frames": index,
              "source_pts_span_seconds": pts[-1] - pts[0],
              "grid_detection": grid_summary(crop, pts, candidates, flags)}
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


def extract_sample(video, ffmpeg, crop, requested_time):
    """Decode the first existing VFR frame at/after a requested source time.

    Keeping source PTS avoids silently presenting the seek request as an actual
    captured frame time. No frame-rate conversion or frame interpolation occurs.
    """
    if not math.isfinite(requested_time) or requested_time < 0:
        raise ValueError("Sample time must be finite and nonnegative")
    x, y, width, height = crop
    process = subprocess.run([
        str(ffmpeg), "-loglevel", "info", "-copyts", "-ss", str(requested_time),
        "-i", str(video), "-frames:v", "1", "-vf",
        f"crop={width}:{height}:{x}:{y},showinfo", "-fps_mode", "passthrough",
        "-f", "image2pipe", "-vcodec", "png", "-threads", "2", "-",
    ], capture_output=True, check=True)
    times = re.findall(r"\bn:\s*\d+.*?pts_time:([\d.e+-]+)",
                       process.stderr.decode(errors="replace"))
    if not times or not process.stdout:
        raise ValueError(f"No decoded frame at {requested_time:g} seconds")
    picture = Image.open(io.BytesIO(process.stdout)).convert("RGB")
    return picture, float(times[0])


def compare_samples(video, reference, ffmpeg, crop, prefix, times, reference_offset):
    """Annotate paired source frames without asserting synchronized captures.

    The central blue centroid is a small diagnostic of the selected hull only;
    it is not a network-jitter or cosmetic-correctness score. The crop must place
    the same isolated hull at its centre, at the same zoom, on both sides.
    """
    width, height = crop[2:]
    tile_width, tile_height = width * 2, height * 2
    contact = Image.new("RGB", (tile_width * len(times), tile_height * 2 + 100), "#101820")
    draw = ImageDraw.Draw(contact)
    draw.text((8, 8), "Paired source frames: reference above, target below. Crops magnified 2x.", fill="white")
    draw.text((8, 24), f"Reference offset {reference_offset:+.3f}s; first capture alignment is approximate. Source PTS shown.", fill="white")
    samples = []
    for column, requested in enumerate(times):
        sample = {"requested_target_time_seconds": requested}
        for row, (label, path, offset) in enumerate([
                ("reference", reference, reference_offset), ("target", video, 0)]):
            picture, pts = extract_sample(path, ffmpeg, crop, requested + offset)
            contact.paste(picture.resize((tile_width, tile_height), Image.Resampling.NEAREST),
                          (column * tile_width, 60 + row * (tile_height + 20)))
            draw.text((column * tile_width + 8, 44 + row * (tile_height + 20)),
                      f"{path.name} PTS {pts:.3f}s", fill="white")
            rgb = np.asarray(picture).astype(np.int16)
            red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
            yy, xx = np.indices((height, width))
            mask = ((blue > 105) & (green > 65) & (blue - red > 32) & (blue - green > 12)
                    & ((xx - width / 2) ** 2 + (yy - height / 2) ** 2 < 25 ** 2))
            ys, xs = np.nonzero(mask)
            sample[label] = {"video": str(path.resolve()), "source_pts_seconds": pts,
                "central_blue_pixels": len(xs),
                "central_blue_centroid_full_capture_xy":
                    [float(xs.mean() + crop[0]), float(ys.mean() + crop[1])] if len(xs) else None}
        samples.append(sample)
    report = {"crop": crop, "reference_offset_seconds": reference_offset, "samples": samples,
        "limits": ["Offset is an assumption; recorder process start does not establish exact first capture time.",
                   "Blue centroid requires the same isolated hull and zoom; damaged or overlapping ships change it.",
                   "Particle randomness and update cadence prevent pixel-for-pixel equivalence.",
                   "These stills show placement and appearance; motion continuity requires the recordings/traces."]}
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    contact.save(prefix.with_name(prefix.name + "-detail").with_suffix(".png"))
    print(json.dumps(report, indent=2))


def relative_measurements(tracks, target_id, reference_id, window, pixel_scale):
    """Remove camera translation; the reference must be the same tracked ship.

    The 200 ms chord residual highlights oscillation, but deliberate acceleration
    also contributes. It is intentionally not called a network jitter score.
    """
    by_id = {track["track"]: track for track in tracks}
    if target_id not in by_id or reference_id not in by_id:
        raise ValueError("Requested relative-motion track not present")
    reference = {row["frame"]: row for row in by_id[reference_id]["observations"]}
    rows = [{"time": row["time"], "x": (row["x"] - reference[row["frame"]]["x"]) * pixel_scale,
             "y": (row["y"] - reference[row["frame"]]["y"]) * pixel_scale}
            for row in by_id[target_id]["observations"] if row["frame"] in reference
            and window[0] <= row["time"] <= window[1]]
    if len(rows) < 10:
        raise ValueError("Too few simultaneous observations in relative-motion window")
    data = np.array([[row["time"], row["x"], row["y"]] for row in rows])
    residuals = []
    for index, row in enumerate(data):
        before = index - 1
        while before >= 0 and row[0] - data[before, 0] < .1:
            before -= 1
        after = index + 1
        while after < len(data) and data[after, 0] - row[0] < .1:
            after += 1
        if before >= 0 and after < len(data):
            chord = data[before, 1:] + (data[after, 1:] - data[before, 1:]) * (
                (row[0] - data[before, 0]) / (data[after, 0] - data[before, 0]))
            residuals.append(float(np.linalg.norm(row[1:] - chord)))
    return {"target_track": target_id, "reference_track": reference_id,
            "window_seconds": [rows[0]["time"], rows[-1]["time"]],
            "pixel_scale": pixel_scale, "samples": len(rows),
            "relative_x_extent_px": float(np.ptp(data[:, 1])),
            "relative_y_extent_px": float(np.ptp(data[:, 2])),
            "chord_200ms_residual_px_p50_p95_max": np.percentile(residuals, [50, 95, 100]).tolist(),
            "observations": rows,
            "limits": "Removes camera translation, but zoom, ship animation and real acceleration remain."}


def plot_relative(relative, video, prefix):
    rows = relative["observations"]
    output = Image.new("RGB", (1400, 460), "#111720")
    draw = ImageDraw.Draw(output)
    left, top, right, bottom = 75, 60, 1360, 400
    xmin, xmax = rows[0]["time"], rows[-1]["time"]
    ymin, ymax = min(row["x"] for row in rows) - 5, max(row["x"] for row in rows) + 5
    for value in np.linspace(ymin, ymax, 6):
        y = bottom - (bottom - top) * (value - ymin) / (ymax - ymin)
        draw.line((left, y, right, y), fill="#293341")
        draw.text((5, y - 5), f"{value:.1f}", fill="#c2c9d1")
    for second in np.linspace(xmin, xmax, 11):
        x = left + (right - left) * (second - xmin) / (xmax - xmin)
        draw.line((x, top, x, bottom), fill="#293341")
        draw.text((x - 8, bottom + 8), f"{second:.1f}", fill="#c2c9d1")
    points = [(left + (right - left) * (row["time"] - xmin) / (xmax - xmin),
               bottom - (bottom - top) * (row["x"] - ymin) / (ymax - ymin)) for row in rows]
    draw.line(points, fill="#48beff", width=2)
    draw.text((75, 18), f"{video.name}: track {relative['target_track']} X relative to track "
              f"{relative['reference_track']} (scaled pixels, actual capture PTS)", fill="white")
    draw.text((75, 436), relative["limits"], fill="#c2c9d1")
    output.save(prefix.with_name(prefix.name + "-relative").with_suffix(".png"))


def analyse(video, ffmpeg, crop, prefix, relative_pair=None, window=(0, float("inf")), pixel_scale=1):
    xoff, yoff, width, height = crop
    tracks, captures = [], []
    try:
        grid_patch, grid_size = grid_patch_geometry(crop)
    except ValueError:
        grid_patch, grid_size = None, None
    grid_candidates, grid_flags = [], []
    with tempfile.TemporaryFile(mode="w+b") as diagnostics:
        process = subprocess.Popen(
            [str(ffmpeg), "-hide_banner", "-i", str(video), "-an", "-vf",
             f"crop={width}:{height}:{xoff}:{yoff},showinfo", "-fps_mode",
             "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=diagnostics)
        index = 0
        while True:
            data = read_exact(process.stdout, width * height * 3)
            if not data:
                break
            if len(data) != width * height * 3:
                raise RuntimeError("Truncated decoded frame")
            frame = np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            candidate = None
            if grid_patch is not None:
                gx, gy, gw, gh = grid_patch
                scene_patch = frame[gy:gy + gh, gx:gx + gw]
                if scene_patch.shape[:2] != (grid_size[1], grid_size[0]):
                    scene_patch = np.asarray(Image.fromarray(scene_patch).resize(tuple(grid_size), Image.Resampling.NEAREST))
                candidate = grid_candidate(scene_patch)
            grid_flags.append(candidate is not None)
            if candidate is not None and len(grid_candidates) < 256:
                grid_candidates.append(dict(candidate, frame=index))
            detections = detect(frame)
            assignments = []
            for tid, track in enumerate(tracks):
                if index - track[-1]["frame"] > 10:
                    continue
                last = track[-1]
                for did, detection in enumerate(detections):
                    distance = math.hypot(last["x"] - detection["x"],
                                          last["y"] - detection["y"])
                    ratio = detection["area"] / max(last["area"], 1)
                    if distance < 90 and .35 < ratio < 2.8:
                        assignments.append((distance, tid, did))
            used_tracks, used_detections = set(), set()
            for distance, tid, did in sorted(assignments):
                if tid in used_tracks or did in used_detections:
                    continue
                tracks[tid].append(dict(detections[did], frame=index))
                used_tracks.add(tid)
                used_detections.add(did)
            for did, detection in enumerate(detections):
                if did not in used_detections:
                    tracks.append([dict(detection, frame=index)])
            # Save only a small set of cropped frames, not another video.
            if index % 80 == 0:
                captures.append((index, Image.fromarray(frame).copy()))
            index += 1
        if process.wait() != 0:
            diagnostics.seek(0)
            raise RuntimeError(diagnostics.read().decode(errors="replace")[-4000:])
        diagnostics.seek(0)
        log = diagnostics.read().decode(errors="replace")
    pts = [float(value) for value in re.findall(r"\bn:\s*\d+.*?pts_time:([\d.e+-]+)", log)]
    if len(pts) != index:
        raise RuntimeError(f"PTS/frame mismatch: {len(pts)} / {index}")
    report_tracks = []
    for tid, track in enumerate(tracks):
        if len(track) < 20:
            continue
        observations = []
        for record in track:
            record["time"] = pts[record["frame"]]
            record["x"] += xoff
            record["y"] += yoff
        consecutive = [(a, b) for a, b in zip(track, track[1:])
                       if b["frame"] == a["frame"] + 1]
        displacements, velocities, speeds, angle_steps = [], [], [], []
        for a, b in consecutive:
            dt = b["time"] - a["time"]
            if dt <= 0:
                continue
            delta = np.array([b["x"] - a["x"], b["y"] - a["y"]])
            distance = float(np.linalg.norm(delta))
            displacements.append(distance)
            velocities.append(delta / dt)
            speeds.append(distance / dt)
            angle = (b["angle"] - a["angle"] + math.pi / 2) % math.pi - math.pi / 2
            angle_steps.append(abs(math.degrees(angle)))
            observations.append({"time": b["time"], "dt": dt, "pixels": distance,
                                 "speed": distance / dt, "angle_degrees": math.degrees(angle)})
        moving = np.array(displacements) > .75
        ordered = sorted(observations, key=lambda value: value["pixels"], reverse=True)
        long_static_runs = []
        run = []
        for observation in observations:
            if observation["pixels"] < .15:
                run.append(observation)
            else:
                if run and sum(item["dt"] for item in run) > .1:
                    long_static_runs.append({"start": run[0]["time"] - run[0]["dt"],
                                             "duration": sum(item["dt"] for item in run)})
                run = []
        # Only mark a pause suspect if moving samples flank it within 150 ms.
        # Actual stops at the beginning/end of the fixture are excluded.
        suspect_pauses = []
        for static in long_static_runs:
            start, end = static["start"], static["start"] + static["duration"]
            before = [o for o in observations if start - .15 <= o["time"] < start
                      and o["pixels"] > .75]
            after = [o for o in observations if end < o["time"] <= end + .15
                     and o["pixels"] > .75]
            if before and after:
                suspect_pauses.append(static)
        report_tracks.append({"track": tid, "samples": len(track),
            "start": track[0]["time"], "end": track[-1]["time"],
            "initial_xy": [track[0]["x"], track[0]["y"]],
            "final_xy": [track[-1]["x"], track[-1]["y"]],
            "moving_samples_over_0_75px": int(moving.sum()),
            "displacement_px_p50_p95_max": np.percentile(displacements, [50, 95, 100]).tolist(),
            "angle_step_degrees_p50_p95_max": np.percentile(angle_steps, [50, 95, 100]).tolist(),
            "largest_steps": ordered[:12], "suspect_pause_runs": suspect_pauses,
            "observations": track})
    intervals = np.diff(pts)
    report = {"video": str(video.resolve()), "source_frames": index,
        "source_pts_span_seconds": pts[-1] - pts[0],
        "capture_interval_ms_p50_p95_max": (np.percentile(intervals, [50, 95, 100]) * 1000).tolist(),
        "capture_gaps_over_50ms": int((intervals > .0501).sum()),
        "crop": crop, "tracks": report_tracks,
        "grid_detection": grid_summary(crop, pts, grid_candidates, grid_flags) if grid_patch is not None
            else {"available": False, "reason": "Scene crop is too small for the bounded off-centre detector patch"},
        "limits": ["Screen coordinates include camera movement.",
                   "Capture timestamps are variable rate; render FPS cannot be inferred.",
                   "Colour segmentation is tuned to stock blue/white ships and may merge overlapping ships.",
                   "PCA angle is modulo 180 degrees and noisy for symmetric or damaged shapes.",
                   "A discontinuity can be input, collision, camera correction or replication; it is not proof alone."]}
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if relative_pair:
        report["relative_motion"] = relative_measurements(report_tracks, *relative_pair, window, pixel_scale)
        plot_relative(report["relative_motion"], video, prefix)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    thumbw, thumbh = 650, 350
    contact = Image.new("RGB", (thumbw * 3, (thumbh + 28) * math.ceil(len(captures) / 3)), "#121820")
    draw = ImageDraw.Draw(contact)
    for position, (frame_index, capture) in enumerate(captures):
        left, top = (position % 3) * thumbw, (position // 3) * (thumbh + 28)
        contact.paste(capture.resize((thumbw, thumbh)), (left, top + 28))
        draw.text((left + 8, top + 7), f"{video.name}  {pts[frame_index]:.3f}s", fill="white")
    contact.save(prefix.with_suffix(".png"))
    compact = dict(report, tracks=[{key: value for key, value in t.items()
                                   if key != "observations"} for t in report_tracks])
    if "relative_motion" in compact:
        compact["relative_motion"] = {key: value for key, value in report["relative_motion"].items()
                                      if key != "observations"}
    print(json.dumps(compact, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--ffmpeg", type=Path, required=True)
    parser.add_argument("--crop", type=int, nargs=4, default=[320, 0, 1300, 700],
                        metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--relative-pair", type=int, nargs=2, metavar=("TARGET_TRACK", "REFERENCE_TRACK"))
    parser.add_argument("--window", type=float, nargs=2, default=[0, float("inf")], metavar=("START", "END"))
    parser.add_argument("--pixel-scale", type=float, default=1,
                        help="Scale relative-motion measurements for comparisons across resolutions")
    parser.add_argument("--compare-video", type=Path, help="Extract paired stills with this reference video")
    parser.add_argument("--sample-times", type=float, nargs="+", help="Target source times for paired stills")
    parser.add_argument("--compare-offset-seconds", type=float, default=0,
                        help="Reference source time minus target source time; explicitly approximate")
    parser.add_argument("--grid-only", action="store_true",
                        help="Decode only a bounded flight-scene patch and report grid pattern candidates")
    args = parser.parse_args()
    if args.grid_only:
        if args.compare_video or args.relative_pair:
            parser.error("--grid-only is separate from paired still and ship tracking modes")
        analyse_grid_only(args.video, args.ffmpeg, args.crop, args.output)
        return
    if args.compare_video:
        if not args.sample_times or not math.isfinite(args.compare_offset_seconds):
            parser.error("--compare-video needs --sample-times and a finite offset")
        compare_samples(args.video, args.compare_video, args.ffmpeg, args.crop, args.output,
                        args.sample_times, args.compare_offset_seconds)
        return
    analyse(args.video, args.ffmpeg, args.crop, args.output, args.relative_pair, args.window, args.pixel_scale)


if __name__ == "__main__":
    main()
