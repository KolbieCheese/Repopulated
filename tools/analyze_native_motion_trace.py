"""Analyse bounded presentation-trace telemetry from native host/client tests.

This measures actual Body render points, not only swap/render FPS. Common-clock
comparison is valid only when both traces were recorded on the same computer.
Pauses and corrections are observations: collisions and deliberate input can
also cause them, so pair the report with the recorded video and fixture phases.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path


FIELDS = ("ident", "seq", "now_ms", "sampled_ms", "raw_x", "raw_y", "raw_vx", "raw_vy",
          "raw_angle", "raw_angular", "shown_x", "shown_y", "shown_angle", "shown_vx",
          "shown_vy", "shown_angular", "render_x", "render_y", "render_angle", "present_call")


def percentiles(values):
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return None
    output = []
    for fraction in (.5, .95, 1):
        index = (len(values) - 1) * fraction
        lo, hi = math.floor(index), math.ceil(index)
        output.append(values[lo] + (values[hi] - values[lo]) * (index - lo))
    return output


def wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def magnitude(x, y):
    return math.hypot(x, y)


def presentation_timing(records):
    """Compare observed hook stages without inventing missing camera samples.

    Camera and draw hooks can run on different native threads. A cached camera
    stage from a different display frame is reported separately, never treated
    as a same-frame camera/render delay.
    """
    paired, same_frame, frame_offsets, draw_age, camera_age, view_age = [], [], [], [], [], []
    stages = {"camera": {}, "draw": {}}
    view_times, visual_sequences, health_sequences, motion_visual_gaps = [], [], [], []
    stage_threads = set()
    for record in records:
        metadata = record.get("presentationStages") or {}
        camera, draw = metadata.get("camera"), metadata.get("draw")
        valid_rows = [values for values in record.get("rows", [])
                      if isinstance(values, list) and len(values) == 20 and values[0]
                      and isinstance(values[2], (int, float)) and math.isfinite(values[2])]
        now = valid_rows[0][2] if valid_rows else None
        for name in stages:
            stage = metadata.get(name)
            if (isinstance(stage, dict) and isinstance(stage.get("atMs"), (int, float))
                    and math.isfinite(stage["atMs"])):
                stages[name][stage["atMs"]] = stage
                if now is not None:
                    (camera_age if name == "camera" else draw_age).append(now - stage["atMs"])
        if (isinstance(camera, dict) and isinstance(draw, dict)
                and all(isinstance(stage.get("atMs"), (int, float)) and math.isfinite(stage["atMs"])
                        for stage in (camera, draw))):
            elapsed = draw["atMs"] - camera["atMs"]
            paired.append(elapsed)
            stage_threads.add((camera.get("thread"), draw.get("thread")))
            if all(isinstance(stage.get("frame"), (int, float)) for stage in (camera, draw)):
                frame_offsets.append(draw["frame"] - camera["frame"])
                if camera["frame"] == draw["frame"]:
                    same_frame.append(elapsed)
        source_time = record.get("viewSourceTimeMs")
        if (now is not None and isinstance(source_time, (int, float))
                and math.isfinite(source_time) and source_time > 0):
            view_age.append(now - source_time)
            view_times.append(source_time)
        visual, health = record.get("visualFrameSeq"), record.get("healthFrameSeq")
        if isinstance(visual, (int, float)) and math.isfinite(visual) and visual > 0:
            visual_sequences.append(visual)
            if valid_rows:
                motion_visual_gaps.append(max(values[1] for values in valid_rows) - visual)
        if isinstance(health, (int, float)) and math.isfinite(health) and health > 0:
            health_sequences.append(health)
    if not any(stages.values()) and not view_age:
        return None
    durations, results = {}, {}
    for name, values in stages.items():
        completed = [stage for stage in values.values()
                     if isinstance(stage.get("finishedAtMs"), (int, float))
                     and math.isfinite(stage["finishedAtMs"])]
        durations[name] = percentiles(stage["finishedAtMs"] - stage["atMs"] for stage in completed)
        returns = [stage["result"] for stage in values.values()
                   if isinstance(stage.get("result"), (int, float)) and math.isfinite(stage["result"])]
        results[name] = {"successful_samples": sum(result >= 0 for result in returns),
            "successful_root_count_p50_p95_max": percentiles(result for result in returns if result >= 0),
            "negative_return_counts": {str(result): returns.count(result) for result in sorted(set(returns))
                                       if result < 0}}
    return {"distinct_camera_stages": len(stages["camera"]), "distinct_draw_stages": len(stages["draw"]),
        "paired_trace_samples": len(paired), "same_frame_camera_draw_samples": len(same_frame),
        "draw_minus_camera_start_ms_all_p50_p95_max": percentiles(paired),
        "draw_minus_camera_start_ms_same_frame_p50_p95_max": percentiles(same_frame),
        "draw_minus_camera_frame_offset_p50_p95_max": percentiles(frame_offsets),
        "post_draw_trace_minus_draw_start_ms_p50_p95_max": percentiles(draw_age),
        "post_draw_trace_minus_camera_start_ms_p50_p95_max": percentiles(camera_age),
        "trace_minus_view_source_time_ms_p50_p95_max": percentiles(view_age),
        "view_source_clock_rewinds": sum(b < a - .001 for a, b in zip(view_times, view_times[1:])),
        "visual_frame_sequence_rewinds": sum(b < a for a, b in zip(visual_sequences, visual_sequences[1:])),
        "health_frame_sequence_rewinds": sum(b < a for a, b in zip(health_sequences, health_sequences[1:])),
        "latest_motion_minus_selected_visual_sequence_p50_p95_max": percentiles(motion_visual_gaps),
        "selected_visual_frame_source_age_ms": None,
        "presenter_stage_duration_ms_p50_p95_max": durations, "presenter_stage_result_counts": results,
        "camera_draw_thread_pairs": sorted(stage_threads, key=repr),
        "limits": "QPC hook times measure native camera/draw ordering, not compositor display time. "
                  "Only equal display-frame indices are used for same-frame skew; other cached stages remain explicit. "
                  "View source age also includes the native source-clock offset estimate. "
                  "Selected visual frame timestamps are absent, so its exact age is not inferred from sequence gaps."}


def comparison_view_summary(records):
    samples = [record for record in records if isinstance(record.get("comparisonView"), dict)]
    if not samples:
        return None
    statuses, actual_errors, view_errors, focus_ages, viewports, zooms = {}, [], [], [], set(), []
    for record in samples:
        view = record["comparisonView"]
        status = view.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
        if not view.get("applied"):
            continue
        centre = record.get("center")
        target = next((row for row in record.get("rows", []) if isinstance(row, list)
                       and len(row) == 20 and row[0] == view.get("ident")), None)
        if (isinstance(centre, list) and len(centre) == 2 and target is not None
                and all(isinstance(view.get(key), (int, float)) for key in ("x", "y"))):
            actual_errors.append(magnitude(view["x"] - centre[0] - target[16],
                                           view["y"] - centre[1] - target[17]))
            if isinstance(view.get("sourceAtMs"), (int, float)):
                focus_ages.append(target[2] - view["sourceAtMs"])
        observed = record.get("view") or {}
        if all(isinstance(view.get(key), (int, float)) and isinstance(observed.get(key), (int, float))
               for key in ("x", "y")):
            view_errors.append(magnitude(view["x"] - observed["x"], view["y"] - observed["y"]))
        viewport = view.get("viewport")
        if isinstance(viewport, list) and len(viewport) == 4:
            viewports.add(tuple(viewport))
        if isinstance(observed.get("zoom"), (int, float)):
            zooms.append(observed["zoom"])
    return {"sampled_records": len(samples), "sampled_status_counts": statuses,
        "cumulative_counts_at_last_trace": samples[-1]["comparisonView"].get("counts"),
        "viewport_values": sorted(viewports), "zoom_p50_p95_max": percentiles(zooms),
        "copied_view_minus_trace_view_position_units_p50_p95_max": percentiles(view_errors),
        "focus_minus_target_actual_render_units_p50_p95_max": percentiles(actual_errors),
        "trace_minus_focus_sample_ms_p50_p95_max": percentiles(focus_ages),
        "limits": "Focus is sampled before DrawGame and actual body fields after DrawGame. "
                  "Host native render interpolation may change the body point between those calls. "
                  "These coordinates verify the fixture; actual on-screen centroids still need video inspection."}


def runtime_clock_summary(records):
    clocks, pacing = {}, None
    for record in records:
        if not isinstance(record, dict):
            continue
        clock = record.get("presentationClock")
        if (isinstance(clock, dict) and all(isinstance(clock.get(key), (int, float))
                and math.isfinite(clock[key]) for key in
                ("localTimeMs", "sourceOffsetMs", "motionSourceTimeMs", "visualSourceTimeMs"))):
            clocks[clock["localTimeMs"]] = clock
        if isinstance(record.get("framePacing"), dict) and record["framePacing"]:
            pacing = record["framePacing"]
    values = [clocks[time] for time in sorted(clocks)]
    if not values and pacing is None:
        return None
    return {"clock_samples": len(values), "source_offset_ms_p50_p95_max": percentiles(
        row["sourceOffsetMs"] for row in values),
        "motion_source_age_ms_p50_p95_max": percentiles(row["localTimeMs"] - row["sourceOffsetMs"] -
            row["motionSourceTimeMs"] for row in values if row["motionSourceTimeMs"] > 0),
        "selected_visual_source_age_ms_p50_p95_max": percentiles(row["localTimeMs"] - row["sourceOffsetMs"] -
            row["visualSourceTimeMs"] for row in values if row["visualSourceTimeMs"] > 0),
        "motion_source_rewinds": sum(b["motionSourceTimeMs"] < a["motionSourceTimeMs"]
                                     for a, b in zip(values, values[1:])),
        "visual_source_rewinds": sum(b["visualSourceTimeMs"] < a["visualSourceTimeMs"]
                                     for a, b in zip(values, values[1:])),
        "last_cumulative_native_frame_pacing": pacing,
        "limits": "Clock fields retain source-epoch timestamps; age subtracts the reported local/source offset. "
                  "Samples come from geometry-apply reports, not every frame; pacing counters are cumulative."}


def load_trace(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("messages", data.get("records", []))
    if not isinstance(data, list):
        raise ValueError(f"Expected a record list: {path}")
    tracks, rejected, records, presentation_records = {}, 0, 0, []
    for record in data:
        if not isinstance(record, dict) or record.get("type") != "presentation-trace":
            continue
        records += 1
        presentation_records.append(record)
        for values in record.get("rows", []):
            if not isinstance(values, list) or len(values) != 20:
                rejected += 1
                continue
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                       and math.isfinite(v) for v in values):
                rejected += 1
                continue
            if not values[0]:
                continue  # Native trace reports a zero row for an absent root.
            row = dict(zip(FIELDS, values))
            row["ident"] = int(row["ident"])
            row["frame"] = record.get("frame")
            row["sim_seconds"] = record.get("simTimeSeconds")
            row["fixture"] = record.get("fixture") or {}
            row["phase"] = row["fixture"].get("phase")
            row["view"] = record.get("view") or {}
            tracks.setdefault(row["ident"], []).append(row)
    for ident, rows in tracks.items():
        # Keep one sample for each monotonic instant, sorting independently of
        # message-dispatch order. No fake samples fill telemetry gaps.
        distinct = {row["now_ms"]: row for row in rows}
        tracks[ident] = [distinct[t] for t in sorted(distinct)]
    return {"path": str(path.resolve()), "records": records,
            "rejected_rows": rejected, "presentation_timing": presentation_timing(presentation_records),
            "comparison_view": comparison_view_summary(presentation_records),
            "runtime_clock": runtime_clock_summary(data),
            "retained_fixture_elapsed_seconds": (lambda values: [min(values), max(values)] if values else None)(
                [float(record["fixture"]["elapsedSeconds"]) for record in presentation_records
                 if isinstance(record.get("fixture"), dict)
                 and isinstance(record["fixture"].get("elapsedSeconds"), (int, float))
                 and math.isfinite(record["fixture"]["elapsedSeconds"])]),
            "tracks": tracks}


def stop_runs(intervals, min_duration=100):
    result, run = [], []
    for interval in intervals + [None]:
        if interval is not None and interval["stalled"]:
            if run and interval["start_ms"] - run[-1]["end_ms"] > 1:
                if sum(row["dt_ms"] for row in run) >= min_duration:
                    result.append(run_summary(run))
                run = []
            run.append(interval)
        else:
            if run and sum(row["dt_ms"] for row in run) >= min_duration:
                result.append(run_summary(run))
            run = []
    return result


def run_summary(run):
    return {"start_ms": run[0]["start_ms"], "duration_ms": sum(row["dt_ms"] for row in run),
            "phase": run[0]["phase"], "samples": len(run),
            "expected_travel_units": sum(row["expected_travel"] for row in run)}


def track_summary(rows, role):
    if not rows:
        return {}
    intervals, raw_wall_ratios, raw_sim_ratios, sim_wall_ratios = [], [], [], []
    projected_total_seconds, moving_wall_seconds = 0, 0
    render_steps, angle_steps, increments, gaps = [], [], [], []
    for a, b in zip(rows, rows[1:]):
        dt_ms = b["now_ms"] - a["now_ms"]
        if dt_ms <= 0:
            continue
        gaps.append(dt_ms)
        dx, dy = b["render_x"] - a["render_x"], b["render_y"] - a["render_y"]
        distance = magnitude(dx, dy)
        render_steps.append(distance)
        angle_delta = abs(wrap(b["render_angle"] - a["render_angle"]))
        angle_steps.append(math.degrees(angle_delta))
        vx, vy = (a["raw_vx"] + b["raw_vx"]) / 2, (a["raw_vy"] + b["raw_vy"]) / 2
        speed = magnitude(vx, vy)
        expected_travel = speed * dt_ms / 1000
        intervals.append({"start_ms": a["now_ms"], "end_ms": b["now_ms"], "dt_ms": dt_ms,
            "stalled": dt_ms <= 500 and expected_travel > .05 and distance < .001,
            "expected_travel": expected_travel, "phase": b["phase"],
            "render_step": distance, "angular_step_degrees": math.degrees(angle_delta)})
        if speed > 10 and dt_ms <= 250:
            raw_dx, raw_dy = b["raw_x"] - a["raw_x"], b["raw_y"] - a["raw_y"]
            projected_seconds = (raw_dx * vx + raw_dy * vy) / (vx * vx + vy * vy)
            raw_wall_ratios.append(projected_seconds / (dt_ms / 1000))
            projected_total_seconds += projected_seconds
            moving_wall_seconds += dt_ms / 1000
            sa, sb = a["sim_seconds"], b["sim_seconds"]
            if isinstance(sa, (int, float)) and isinstance(sb, (int, float)) and sb > sa:
                sim_dt = sb - sa
                sim_wall_ratios.append(sim_dt / (dt_ms / 1000))
                raw_sim_ratios.append(projected_seconds / sim_dt)
        if b["seq"] != a["seq"] and a["seq"]:
            increments.append(b["now_ms"])
    intended = [row for row in rows if row["present_call"] > 0]
    differences = [magnitude(row["render_x"] - row["shown_x"],
                             row["render_y"] - row["shown_y"]) for row in intended]
    angle_differences = [abs(math.degrees(wrap(row["render_angle"] - row["shown_angle"])))
                         for row in intended]
    source_ages = [row["now_ms"] - row["sampled_ms"] for row in intended if row["sampled_ms"] > 0]
    sequence_intervals = [b - a for a, b in zip(increments, increments[1:])]
    runs = stop_runs(intervals)
    by_phase = {}
    for phase in sorted({row["phase"] for row in rows if row["phase"]}):
        phase_intervals = [row for row in intervals if row["phase"] == phase]
        by_phase[phase] = {"intervals": len(phase_intervals),
            "render_step_units_p50_p95_max": percentiles(row["render_step"] for row in phase_intervals),
            "angle_step_degrees_p50_p95_max": percentiles(row["angular_step_degrees"] for row in phase_intervals),
            "moving_expected_stall_runs": stop_runs(phase_intervals)}
    result = {"samples": len(rows), "monotonic_span_ms": [rows[0]["now_ms"], rows[-1]["now_ms"]],
        "trace_interval_ms_p50_p95_max": percentiles(gaps),
        "fractional_millisecond_timestamp_samples": sum(abs(row["now_ms"] - round(row["now_ms"])) > .00001
                                                        for row in rows),
        "interval_distance_from_15_625ms_grid_p50_p95_max": percentiles(
            abs(value - round(value / 15.625) * 15.625) for value in gaps),
        "render_step_units_p50_p95_max": percentiles(render_steps),
        "angle_step_degrees_p50_p95_max": percentiles(angle_steps),
        "moving_expected_stall_runs": runs,
        "source_age_ms_p50_p95_max": percentiles(source_ages),
        "source_age_over_1000ms_samples": sum(age > 1000 for age in source_ages),
        "motion_sequence_interval_ms_p50_p95_max": percentiles(sequence_intervals),
        "by_phase": by_phase}
    if role == "client":
        previous_positive_seq = None
        sequence_regressions, zeros_after_positive = [], []
        for row in rows:
            if previous_positive_seq is not None and row["seq"] < previous_positive_seq:
                detail = {"now_ms": row["now_ms"], "frame": row["frame"],
                          "fixture": row["fixture"], "previous_positive_sequence": previous_positive_seq,
                          "sequence": row["seq"], "sampled_at_ms": row["sampled_ms"],
                          "raw_minus_render_position_units": magnitude(row["raw_x"] - row["render_x"],
                                                                        row["raw_y"] - row["render_y"])}
                sequence_regressions.append(detail)
                if row["seq"] == 0:
                    zeros_after_positive.append(detail)
            if row["seq"] > 0:
                previous_positive_seq = row["seq"]
        result.update({"render_vs_intended_samples": len(intended),
            "render_vs_intended_position_units_p50_p95_max": percentiles(differences),
            "render_vs_intended_angle_degrees_p50_p95_max": percentiles(angle_differences),
            "render_vs_intended_position_over_0_01_units": sum(value > .01 for value in differences),
            "render_vs_intended_angle_over_0_01_degrees": sum(value > .01 for value in angle_differences),
            "source_sequence_zero_samples": sum(row["seq"] == 0 for row in rows),
            "source_sequence_regression_samples": len(sequence_regressions),
            "source_sequence_zero_after_positive_samples": len(zeros_after_positive),
            "source_sequence_regressions": sequence_regressions[:32],
            "source_sequence_regression_rows_truncated": len(sequence_regressions) > 32,
            "source_sequence_limits": "Sequence reset can reflect startup, interest changes or respawn. "
                "These observations are descriptive; verify stable native identity and the video before interpreting a reset as a defect."})
        if any(age > 1000 for age in source_ages):
            result["warning"] = ("Cached presentation samples are over one second old. Check native root identity, "
                                 "interest membership and renderer activity before interpreting differences; "
                                 "source age alone does not distinguish packet stalls from an absent root.")
    else:
        sim_span = rows[-1]["sim_seconds"] - rows[0]["sim_seconds"] if (
            isinstance(rows[0]["sim_seconds"], (int, float)) and
            isinstance(rows[-1]["sim_seconds"], (int, float))) else None
        wall_span = (rows[-1]["now_ms"] - rows[0]["now_ms"]) / 1000
        result.update({"raw_motion_over_velocity_wall_dt_ratio_p50_p95_max": percentiles(raw_wall_ratios),
            "raw_motion_over_velocity_sim_dt_ratio_p50_p95_max": percentiles(raw_sim_ratios),
            "sim_time_over_wall_time_ratio_p50_p95_max": percentiles(sim_wall_ratios),
            "total_sim_seconds": sim_span, "total_wall_seconds": wall_span,
            "overall_sim_time_over_wall_time_ratio": sim_span / wall_span if sim_span is not None and wall_span else None,
            "aggregate_moving_raw_motion_over_velocity_wall_ratio": projected_total_seconds / moving_wall_seconds
                if moving_wall_seconds else None})
        result["render_minus_raw_along_velocity_time_ms_p50_p95_max"] = percentiles(
            1000 * ((row["render_x"] - row["raw_x"]) * row["raw_vx"] +
                    (row["render_y"] - row["raw_y"]) * row["raw_vy"]) /
            (row["raw_vx"] ** 2 + row["raw_vy"] ** 2)
            for row in rows if magnitude(row["raw_vx"], row["raw_vy"]) > 20)
    return result


def interpolate(rows, times, time_ms, max_gap=250, point="render"):
    index = bisect.bisect_left(times, time_ms)
    if index == 0 or index == len(rows):
        return None
    a, b = rows[index - 1], rows[index]
    span = b["now_ms"] - a["now_ms"]
    if span <= 0 or span > max_gap:
        return None
    fraction = (time_ms - a["now_ms"]) / span
    return {key: a[key] + (b[key] - a[key]) * fraction
            for key in (point + "_x", point + "_y", "raw_vx", "raw_vy")} | {
        point + "_angle": a[point + "_angle"] + wrap(b[point + "_angle"] - a[point + "_angle"]) * fraction}


def in_fixture_window(elapsed, fixture_window):
    return fixture_window is None or (elapsed is not None and fixture_window[0] <= elapsed <= fixture_window[1])


def paired_comparison(client, host, expected_delay_ms=0, fixture_window=None):
    comparisons = {}
    for ident in sorted(set(client["tracks"]) & set(host["tracks"])):
        crows, hrows = client["tracks"][ident], host["tracks"][ident]
        if fixture_window is not None:
            crows = [row for row in crows if in_fixture_window(
                fixture_elapsed(row) - expected_delay_ms / 1000 if fixture_elapsed(row) is not None else None,
                fixture_window)]
        htimes = [row["now_ms"] for row in hrows]
        candidates = []
        lags = set(range(-500, int(max(500, expected_delay_ms + 250)) + 1, 10))
        lags.add(expected_delay_ms)

        def evaluate(lag):
            positions, angles, fixture_times = [], [], []
            for row in crows:
                native = interpolate(hrows, htimes, row["now_ms"] - lag)
                if native is None or magnitude(native["raw_vx"], native["raw_vy"]) <= 10:
                    continue
                positions.append(magnitude(row["render_x"] - native["render_x"],
                                           row["render_y"] - native["render_y"]))
                angles.append(abs(math.degrees(wrap(row["render_angle"] - native["render_angle"]))))
                elapsed = fixture_elapsed(row)
                if elapsed is not None:
                    fixture_times.append(elapsed - expected_delay_ms / 1000)
            if len(positions) >= 20:
                return {"lag_ms": lag, "moving_samples": len(positions),
                    "matched_presented_fixture_elapsed_seconds": [min(fixture_times), max(fixture_times)]
                        if fixture_times else None,
                    "position_error_units_p50_p95_max": percentiles(positions),
                    "angle_error_degrees_p50_p95_max": percentiles(angles)}
            return None

        for lag in sorted(lags):
            value = evaluate(lag)
            if value:
                candidates.append(value)
        if candidates:
            best = min(candidates, key=lambda value: value["position_error_units_p50_p95_max"][0])
            for lag in range(math.floor(best["lag_ms"] - 10), math.ceil(best["lag_ms"] + 10) + 1):
                if lag not in lags:
                    value = evaluate(lag)
                    if value:
                        candidates.append(value)
            best = min(candidates, key=lambda value: value["position_error_units_p50_p95_max"][0])
            comparisons[str(ident)] = {"zero_lag": next((c for c in candidates if c["lag_ms"] == 0), None),
                "expected_display_delay_ms": expected_delay_ms,
                "comparison_fixture_window_seconds": fixture_window,
                "expected_delay_comparison": next((c for c in candidates if c["lag_ms"] == expected_delay_ms), None),
                "best_constant_position_lag": best,
                "lag_search_resolution_ms": "10 ms coarse, 1 ms around the best coarse candidate",
                "limits": "Positive lag means client matches past host poses; negative means it matches future poses. "
                          "Expected-delay comparison uses the declared presentation delay and does not count it as an error. "
                          "Fit compares post-draw render points; host render/physics interpolation also changes effective pose lag. "
                          "Fit is descriptive; buffering, real input changes and variable latency remain."}
    return comparisons


def fixture_elapsed(row):
    value = row["fixture"].get("elapsedSeconds")
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def phase_motion(rows, fixture_start_ms, point="render", half_window_ms=100, cycle=None, time_shift_ms=0,
                 fixture_window=None):
    """Phase-align 200 ms central derivatives away from input transitions.

    'forward' samples start two seconds into each eight-second phase, excluding
    startup acceleration. This exposes correction acceleration at steady input;
    actual collisions still need checking in video/native raw traces.
    """
    times, groups = [row["now_ms"] for row in rows], {}
    for row in rows:
        elapsed = (row["now_ms"] - time_shift_ms - fixture_start_ms) / 1000
        if elapsed < 0:
            continue
        if not in_fixture_window(elapsed, fixture_window):
            continue
        if cycle is not None and int(elapsed // 20) != cycle:
            continue
        phase_seconds = elapsed % 20
        if 2 <= phase_seconds <= 7.7:
            phase = "forward_steady"
        elif 8.3 <= phase_seconds <= 15.7:
            phase = "turn"
        elif 16.3 <= phase_seconds <= 19.7:
            phase = "brake"
        else:
            continue
        past = interpolate(rows, times, row["now_ms"] - half_window_ms, point=point)
        future = interpolate(rows, times, row["now_ms"] + half_window_ms, point=point)
        if past is None or future is None:
            continue
        h = half_window_ms / 1000
        centre_x, centre_y = row[point + "_x"], row[point + "_y"]
        speed = magnitude(future[point + "_x"] - past[point + "_x"],
                          future[point + "_y"] - past[point + "_y"]) / (2 * h)
        acceleration = magnitude(future[point + "_x"] - 2 * centre_x + past[point + "_x"],
                                 future[point + "_y"] - 2 * centre_y + past[point + "_y"]) / (h * h)
        angular_speed = abs(wrap(future[point + "_angle"] - past[point + "_angle"])) / (2 * h)
        angular_accel = abs(wrap(future[point + "_angle"] - row[point + "_angle"]) -
                            wrap(row[point + "_angle"] - past[point + "_angle"])) / (h * h)
        groups.setdefault(phase, []).append([speed, acceleration, math.degrees(angular_speed),
                                             math.degrees(angular_accel)])
    return {phase: {"samples": len(values),
        "wall_speed_units_per_second_p50_p95_max": percentiles(value[0] for value in values),
        "wall_acceleration_units_per_second2_p50_p95_max": percentiles(value[1] for value in values),
        "wall_angular_speed_degrees_per_second_p50_p95_max": percentiles(value[2] for value in values),
        "wall_angular_acceleration_degrees_per_second2_p50_p95_max": percentiles(value[3] for value in values)}
        for phase, values in groups.items()}


def paired_phase_motion(crows, hrows, fixture_start_ms, expected_delay_ms=0, cycle=None, fixture_window=None):
    """Compare derivatives at identical ship times, including collisions on both sides.

    The native host is resampled at every eligible client observation minus the
    declared display delay. This avoids comparing unrelated host/client sample
    populations when native tracing cadence or interest membership differs.
    """
    ctimes, htimes = [r["now_ms"] for r in crows], [r["now_ms"] for r in hrows]
    groups = {}

    def derivative(past, centre, future, point):
        acceleration = ((future[point + "_x"] - 2 * centre[point + "_x"] + past[point + "_x"]) / .01,
                        (future[point + "_y"] - 2 * centre[point + "_y"] + past[point + "_y"]) / .01)
        angular = math.degrees(wrap(future[point + "_angle"] - centre[point + "_angle"]) -
                               wrap(centre[point + "_angle"] - past[point + "_angle"])) / .01
        return acceleration, angular

    for row in crows:
        host_now = row["now_ms"] - expected_delay_ms
        elapsed = (host_now - fixture_start_ms) / 1000
        if elapsed < 0 or (cycle is not None and int(elapsed // 20) != cycle):
            continue
        if not in_fixture_window(elapsed, fixture_window):
            continue
        seconds = elapsed % 20
        phase = ("forward_steady" if 2 <= seconds <= 7.7 else
                 "turn" if 8.3 <= seconds <= 15.7 else
                 "brake" if 16.3 <= seconds <= 19.7 else None)
        if phase is None:
            continue
        host = [interpolate(hrows, htimes, host_now + shift) for shift in (-100, 0, 100)]
        if any(value is None for value in host):
            continue
        haccel, hang = derivative(*host, "render")
        for point in ("render", "shown"):
            past = interpolate(crows, ctimes, row["now_ms"] - 100, point=point)
            future = interpolate(crows, ctimes, row["now_ms"] + 100, point=point)
            if past is None or future is None:
                continue
            caccel, cang = derivative(past, row, future, point)
            groups.setdefault(phase, {}).setdefault(point, []).append([
                magnitude(*haccel), magnitude(*caccel),
                magnitude(caccel[0] - haccel[0], caccel[1] - haccel[1]),
                abs(hang), abs(cang), abs(cang - hang)])
    names = ("host_acceleration_units_per_second2", "client_acceleration_units_per_second2",
             "acceleration_vector_error_units_per_second2", "host_angular_acceleration_degrees_per_second2",
             "client_angular_acceleration_degrees_per_second2", "angular_acceleration_error_degrees_per_second2")
    return {phase: {point: {"matched_samples": len(values), **{
        name + "_p50_p95_max": percentiles(value[index] for value in values)
        for index, name in enumerate(names)}} for point, values in points.items()}
        for phase, points in groups.items()}


def analyse(client_path, host_path=None, common_clock=False, cycle=None, expected_delay_ms=0, fixture_window=None):
    client = load_trace(client_path)
    host = load_trace(host_path) if host_path else None
    report = {"client": {key: value for key, value in client.items() if key != "tracks"},
        "limits": ["Telemetry is sampled after DrawGame; it does not measure display compositor latency.",
                   "Traces are rate-limited to roughly 33 ms; 200 ms derivatives cannot certify every 60 Hz render frame.",
                   "Stalls require nonzero advertised velocity, but collision/input semantics need video verification.",
                   "Velocity clock ratios project motion onto mean velocity; sharp turns/collisions can distort them.",
                   "Common monotonic clock comparison requires host and client on the same computer."]}
    report["client"]["tracks"] = {str(ident): track_summary(rows, "client")
                                    for ident, rows in client["tracks"].items()}
    if host:
        report["host"] = {key: value for key, value in host.items() if key != "tracks"}
        report["host"]["tracks"] = {str(ident): track_summary(rows, "host")
                                     for ident, rows in host["tracks"].items()}
        if common_clock:
            report["paired_common_clock"] = paired_comparison(client, host, expected_delay_ms, fixture_window)
            fixture_starts = [row["now_ms"] - fixture_elapsed(row) * 1000
                              for rows in client["tracks"].values() for row in rows
                              if fixture_elapsed(row) is not None]
            if fixture_starts:
                start = percentiles(fixture_starts)[0]
                report["phase_aligned_motion"] = {"fixture_start_monotonic_ms": start,
                    "central_derivative_window_ms": 200,
                    "selected_fixture_cycle": cycle,
                    "comparison_fixture_window_seconds": fixture_window,
                    "client_presentation_delay_ms": expected_delay_ms,
                    "limits": "World-unit derivatives on a shared wall clock; forward excludes first two seconds/input boundaries. "
                              "Client phase labels subtract the declared display delay; host phases use current host time. "
                              "Real collision acceleration and render quantization remain.",
                    "tracks": {str(ident): {"client_render": phase_motion(client["tracks"][ident], start, cycle=cycle,
                                                                         time_shift_ms=expected_delay_ms,
                                                                         fixture_window=fixture_window),
                        "client_intended": phase_motion(client["tracks"][ident], start, "shown", cycle=cycle,
                                                         time_shift_ms=expected_delay_ms, fixture_window=fixture_window),
                        "host_render": phase_motion(host["tracks"][ident], start, cycle=cycle,
                                                     fixture_window=fixture_window)}
                        for ident in sorted(set(client["tracks"]) & set(host["tracks"]))}}
                report["phase_aligned_motion"]["paired_derivatives"] = {
                    str(ident): paired_phase_motion(client["tracks"][ident], host["tracks"][ident], start,
                                                   expected_delay_ms, cycle, fixture_window)
                    for ident in sorted(set(client["tracks"]) & set(host["tracks"]))}
                cycles = sorted({int(elapsed // 20) for rows in client["tracks"].values() for row in rows
                                 if fixture_elapsed(row) is not None
                                 for elapsed in [fixture_elapsed(row) - expected_delay_ms / 1000]
                                 if elapsed >= 0 and in_fixture_window(elapsed, fixture_window)
                                 and (cycle is None or int(elapsed // 20) == cycle)})
                report["paired_common_clock_by_fixture_cycle"] = {
                    str(value): paired_comparison(client, host, expected_delay_ms,
                        [max(value * 20, fixture_window[0] if fixture_window else 0),
                         min((value + 1) * 20, fixture_window[1] if fixture_window else (value + 1) * 20)])
                    for value in cycles}
    return report


def plot_presentation(client_path, host_path, output, window=(2, 6), ident=0x70000002, expected_delay_ms=0):
    """Small publication/export artifact; Pillow is optional for this path."""
    from PIL import Image, ImageDraw

    client, host = load_trace(client_path), load_trace(host_path)
    crows, hrows = client["tracks"].get(ident, []), host["tracks"].get(ident, [])
    starts = [row["now_ms"] - fixture_elapsed(row) * 1000 for row in crows
              if fixture_elapsed(row) is not None]
    if not starts or not hrows:
        raise ValueError("Plot needs a native fixture and the same ship on both sides")
    start = percentiles(starts)[0]
    rows = [row for row in crows if window[0] <= (row["now_ms"] - expected_delay_ms - start) / 1000 <= window[1]]
    htimes = [row["now_ms"] for row in hrows]
    if not rows:
        raise ValueError("No observations in plot window")
    native = interpolate(hrows, htimes, rows[0]["now_ms"] - expected_delay_ms)
    if native is None:
        raise ValueError("No common host/client time at plot start")
    speed = magnitude(native["raw_vx"], native["raw_vy"])
    if speed <= .001:
        raise ValueError("Plot start needs nonzero host velocity to define along-flight direction")
    ux, uy = native["raw_vx"] / speed, native["raw_vy"] / speed
    origin = native["render_x"] * ux + native["render_y"] * uy
    curves = {"host render": [], "client intended": [], "client post-draw render": []}
    errors = []
    for row in rows:
        elapsed = (row["now_ms"] - expected_delay_ms - start) / 1000
        native = interpolate(hrows, htimes, row["now_ms"] - expected_delay_ms)
        if native:
            curves["host render"].append((elapsed, native["render_x"] * ux + native["render_y"] * uy - origin))
        curves["client intended"].append((elapsed, row["shown_x"] * ux + row["shown_y"] * uy - origin))
        curves["client post-draw render"].append((elapsed, row["render_x"] * ux + row["render_y"] * uy - origin))
        errors.append((elapsed, (row["render_x"] - row["shown_x"]) * ux +
                                   (row["render_y"] - row["shown_y"]) * uy))
    image = Image.new("RGB", (1500, 840), "#111820")
    draw = ImageDraw.Draw(image)
    colours = {"host render": "#57c8ff", "client intended": "#f5d56e", "client post-draw render": "#ff7070"}

    def pane(data, top, bottom, label):
        ymin = min(y for values in data.values() for x, y in values) - 2
        ymax = max(y for values in data.values() for x, y in values) + 2
        xmin, xmax = window
        for tick in range(9):
            elapsed = xmin + (xmax - xmin) * tick / 8
            x = 80 + 1340 * tick / 8
            draw.line((x, top, x, bottom), fill="#2a3542")
            draw.text((x - 6, bottom + 8), f"{elapsed:.1f}", fill="#d1d5da")
        for tick in range(6):
            value = ymin + (ymax - ymin) * tick / 5
            y = bottom - (bottom - top) * tick / 5
            draw.line((80, y, 1420, y), fill="#2a3542")
            draw.text((8, y - 5), f"{value:.1f}", fill="#d1d5da")
        for name, values in data.items():
            points = [(80 + 1340 * (x - xmin) / (xmax - xmin),
                       bottom - (bottom - top) * (y - ymin) / (ymax - ymin)) for x, y in values]
            if len(points) > 1:
                draw.line(points, fill=colours.get(name, "#ff7070"), width=2)
        draw.text((80, top - 25), label, fill="white")

    pane(curves, 105, 420, "Position projected along initial flight direction (world units)")
    pane({"difference": errors}, 525, 745, "Client actual render point minus intended presentation (world units)")
    draw.text((80, 18), f"Native ship {ident}: fixture {window[0]:g}-{window[1]:g} seconds; "
              f"client aligned to declared {expected_delay_ms:g} ms display delay", fill="white")
    for index, name in enumerate(curves):
        draw.text((80 + index * 400, 55), name, fill=colours[name])
    draw.text((80, 801), "Body fields sampled after DrawGame. Video is still needed to confirm changes reaching the display.", fill="#d1d5da")
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", type=Path, required=True)
    parser.add_argument("--host", type=Path)
    parser.add_argument("--common-clock", action="store_true")
    parser.add_argument("--cycle", type=int, help="Restrict phase derivatives to one 20-second fixture cycle")
    parser.add_argument("--expected-delay-ms", type=float, default=0,
                        help="Declared client presentation delay, used for paired error and phase alignment")
    parser.add_argument("--fixture-window", type=float, nargs=2, metavar=("START", "END"),
                        help="Restrict paired/phase comparisons to these presented fixture seconds; raw tracks stay complete")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plot", type=Path, help="Write a native presentation detail PNG (requires Pillow)")
    parser.add_argument("--plot-window", type=float, nargs=2, default=[2, 6], metavar=("START", "END"))
    parser.add_argument("--plot-ident", type=int, default=0x70000002)
    args = parser.parse_args()
    if not math.isfinite(args.expected_delay_ms) or not 0 <= args.expected_delay_ms <= 2000:
        parser.error("--expected-delay-ms must be finite and between 0 and 2000")
    if args.fixture_window and not (all(math.isfinite(value) for value in args.fixture_window)
                                   and 0 <= args.fixture_window[0] < args.fixture_window[1]):
        parser.error("--fixture-window must contain finite seconds with 0 <= START < END")
    report = analyse(args.client, args.host, args.common_clock, args.cycle, args.expected_delay_ms, args.fixture_window)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    if args.plot:
        if not args.host or not args.common_clock:
            parser.error("--plot needs --host and --common-clock")
        plot_presentation(args.client, args.host, args.plot, args.plot_window, args.plot_ident, args.expected_delay_ms)


if __name__ == "__main__":
    main()
