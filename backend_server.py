from __future__ import annotations

import argparse
import json
import math
import os
import threading
import time
import traceback
import uuid
import warnings
import webbrowser
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import polars as pl
import scipy.stats as scipy_stats
import uvicorn
from bioeq import ParallelDesign
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from pyvivc import pyivivc

ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "3000"))

TIME_GRID = np.round(np.arange(0.0, 24.0 + 0.05, 0.05), 4)
STUDY_TIMES = np.array([0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0])
PROFILE_TICKS = np.array([0.0, 0.5, 1.0, 2.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0])

SEGMENTS = [
    {"key": "stomach", "label": "胃", "ph": 1.6, "transit": 0.85, "surface": 0.28, "agitation": 1.35},
    {"key": "duodenum", "label": "十二指肠", "ph": 5.6, "transit": 0.68, "surface": 0.95, "agitation": 1.08},
    {"key": "jejunum", "label": "空肠", "ph": 6.2, "transit": 0.56, "surface": 1.15, "agitation": 1.0},
    {"key": "ileum", "label": "回肠", "ph": 7.0, "transit": 0.46, "surface": 0.92, "agitation": 0.88},
    {"key": "colon", "label": "结肠", "ph": 7.4, "transit": 0.24, "surface": 0.40, "agitation": 0.56},
]

TISSUES = {
    "liver": {"label": "肝脏", "volume": 1.8, "kin": 0.34, "kout": 0.22},
    "heart": {"label": "心脏", "volume": 5.5, "kin": 0.20, "kout": 0.18},
    "kidney": {"label": "肾脏", "volume": 0.35, "kin": 0.42, "kout": 0.29},
    "muscle": {"label": "肌肉", "volume": 28.0, "kin": 0.07, "kout": 0.05},
    "fat": {"label": "脂肪", "volume": 18.0, "kin": 0.03, "kout": 0.016},
    "vessel": {"label": "血管", "volume": 3.5, "kin": 0.24, "kout": 0.20},
}

REPO_CHECKS = [
    ("PK-Sim 源码仓库", ROOT / "external" / "PK-Sim"),
    ("pyvivc 源码仓库", ROOT / "external" / "pyvivc"),
    ("MoBi 源码仓库", ROOT / "external" / "MoBi"),
    ("OSP IVIVC 示例仓库", ROOT / "external" / "OSP-IVIVC"),
]

PACKAGE_CHECKS = [
    "numpy",
    "scipy",
    "pandas",
    "polars",
    "fastapi",
    "uvicorn",
    "pyvivc",
    "bioeq",
    "matplotlib",
]

DEFAULT_CASE = {
    "name": "真实本地 PBPK/BE 任务",
    "dose": 100.0,
    "weight": 70.0,
    "cl": 15.0,
    "v": 80.0,
    "ka": 1.2,
    "ktr": 1.05,
    "refDiss": 1.00,
    "refSol": 1.00,
    "refPart": 1.00,
    "refFabs": 0.92,
    "testDiss": 0.82,
    "testSol": 0.95,
    "testPart": 1.15,
    "testFabs": 0.86,
    "subjects": 24,
    "trials": 40,
    "clcv": 20.0,
    "vcv": 15.0,
    "kacv": 18.0,
    "food": 1.00,
    "emax": 1.0,
    "ec50": 2.0,
    "hill": 1.2,
    "e0": 0.0,
    "pka": 5.2,
    "logP": 2.4,
    "permeability": 1.4,
    "solubility": 1.0,
}


class CaseInput(BaseModel):
    name: str = DEFAULT_CASE["name"]
    dose: float = DEFAULT_CASE["dose"]
    weight: float = DEFAULT_CASE["weight"]
    cl: float = DEFAULT_CASE["cl"]
    v: float = DEFAULT_CASE["v"]
    ka: float = DEFAULT_CASE["ka"]
    ktr: float = DEFAULT_CASE["ktr"]
    refDiss: float = DEFAULT_CASE["refDiss"]
    refSol: float = DEFAULT_CASE["refSol"]
    refPart: float = DEFAULT_CASE["refPart"]
    refFabs: float = DEFAULT_CASE["refFabs"]
    testDiss: float = DEFAULT_CASE["testDiss"]
    testSol: float = DEFAULT_CASE["testSol"]
    testPart: float = DEFAULT_CASE["testPart"]
    testFabs: float = DEFAULT_CASE["testFabs"]
    subjects: int = DEFAULT_CASE["subjects"]
    trials: int = DEFAULT_CASE["trials"]
    clcv: float = DEFAULT_CASE["clcv"]
    vcv: float = DEFAULT_CASE["vcv"]
    kacv: float = DEFAULT_CASE["kacv"]
    food: float = DEFAULT_CASE["food"]
    emax: float = DEFAULT_CASE["emax"]
    ec50: float = DEFAULT_CASE["ec50"]
    hill: float = DEFAULT_CASE["hill"]
    e0: float = DEFAULT_CASE["e0"]
    pka: float = DEFAULT_CASE["pka"]
    logP: float = DEFAULT_CASE["logP"]
    permeability: float = DEFAULT_CASE["permeability"]
    solubility: float = DEFAULT_CASE["solubility"]


app = FastAPI(title="PBPK BE Local Backend")
JOBS: Dict[str, Dict[str, Any]] = {}
JOB_LOCK = threading.Lock()
SELF_CHECK_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def version_of(package_name: str) -> str:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return "missing"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def serialise(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): serialise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serialise(v) for v in value]
    if isinstance(value, tuple):
        return [serialise(v) for v in value]
    if isinstance(value, np.ndarray):
        return [serialise(v) for v in value.tolist()]
    if isinstance(value, (np.floating, np.float32, np.float64)):
        return float(value)
    if isinstance(value, (np.integer, np.int32, np.int64)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pl.DataFrame):
        return value.to_dicts()
    if isinstance(value, pd.DataFrame):
        return value.to_dict(orient="records")
    return value


def merge_case(payload: Dict[str, Any]) -> Dict[str, Any]:
    case = {**DEFAULT_CASE, **payload}
    case["subjects"] = max(6, int(case["subjects"]))
    case["trials"] = max(4, int(case["trials"]))
    case["dose"] = max(1.0, float(case["dose"]))
    case["weight"] = max(30.0, float(case["weight"]))
    case["food"] = clamp(float(case["food"]), 0.4, 1.8)
    case["solubility"] = clamp(float(case["solubility"]), 0.1, 5.0)
    case["permeability"] = clamp(float(case["permeability"]), 0.1, 5.0)
    case["pka"] = clamp(float(case["pka"]), 0.5, 12.0)
    case["logP"] = clamp(float(case["logP"]), -1.0, 6.0)
    return case


def sample_profile(times: np.ndarray, values: List[float], sample_times: np.ndarray) -> List[float]:
    return np.interp(sample_times, times, np.asarray(values, dtype=float)).tolist()


def t_critical_90(df: int) -> float:
    if df <= 1:
        return 6.3138
    return float(scipy_stats.t.ppf(0.95, df))


def lognormal_mean_one(cv_pct: float, rng: np.random.Generator) -> float:
    cv = max(float(cv_pct), 0.0) / 100.0
    if cv == 0.0:
        return 1.0
    sigma_sq = math.log(1.0 + cv * cv)
    sigma = math.sqrt(sigma_sq)
    mu = -0.5 * sigma_sq
    return float(math.exp(rng.normal(mu, sigma)))


def estimate_seconds(case: Dict[str, Any]) -> int:
    workload = case["subjects"] * case["trials"]
    return int(clamp(round(4 + workload * 0.03), 4, 120))


def create_job(case: Dict[str, Any]) -> Dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "progress": 0.0,
        "phase": "queued",
        "phase_label": "排队中",
        "message": "任务已进入本地队列。",
        "eta_seconds": estimate_seconds(case),
        "estimated_seconds": estimate_seconds(case),
        "created_at": time.time(),
        "started_at": None,
        "finished_at": None,
        "updated_at": time.time(),
        "logs": [{"at": now_iso(), "text": "任务创建，等待后端执行。"}],
        "result": None,
        "error": None,
        "params": case,
    }
    with JOB_LOCK:
        JOBS[job_id] = job
    return job


def append_log(job_id: str, text: str) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        job["logs"].insert(0, {"at": now_iso(), "text": text})
        job["logs"] = job["logs"][:30]
        job["updated_at"] = time.time()


def update_job(job_id: str, *, status: str | None = None, progress: float | None = None, phase: str | None = None, phase_label: str | None = None, message: str | None = None, result: Any | None = None, error: str | None = None) -> None:
    with JOB_LOCK:
        job = JOBS[job_id]
        if status is not None:
            job["status"] = status
            if status == "running" and job["started_at"] is None:
                job["started_at"] = time.time()
            if status in {"completed", "failed"}:
                job["finished_at"] = time.time()
        if progress is not None:
            job["progress"] = float(clamp(progress, 0.0, 1.0))
        if phase is not None:
            job["phase"] = phase
        if phase_label is not None:
            job["phase_label"] = phase_label
        if message is not None:
            job["message"] = message
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        elapsed = 0.0
        if job["started_at"]:
            elapsed = max(0.0, time.time() - job["started_at"])
        if job["status"] == "completed":
            job["eta_seconds"] = 0
        elif job["progress"] >= 0.02 and elapsed > 0:
            remaining = elapsed * (1.0 - job["progress"]) / job["progress"]
            job["eta_seconds"] = int(max(0, round(remaining)))
        else:
            job["eta_seconds"] = job["estimated_seconds"]
        job["updated_at"] = time.time()


def job_snapshot(job_id: str) -> Dict[str, Any]:
    with JOB_LOCK:
        if job_id not in JOBS:
            raise KeyError(job_id)
        return serialise(JOBS[job_id])


def build_formulation(case: Dict[str, Any], formulation: str) -> Dict[str, float]:
    if formulation == "Reference":
        return {
            "diss": float(case["refDiss"]),
            "sol": float(case["refSol"]),
            "part": float(case["refPart"]),
            "fabs": float(case["refFabs"]),
        }
    return {
        "diss": float(case["testDiss"]),
        "sol": float(case["testSol"]),
        "part": float(case["testPart"]),
        "fabs": float(case["testFabs"]),
    }


def simulate_profile(case: Dict[str, Any], formulation: str, subject: Dict[str, float] | None = None) -> Dict[str, Any]:
    subject = subject or {}
    form = build_formulation(case, formulation)
    times = TIME_GRID
    dt = float(times[1] - times[0])

    cl_value = case["cl"] * subject.get("clearance", 1.0)
    volume = case["v"] * subject.get("volume", 1.0)
    ka_base = case["ka"] * subject.get("absorption", 1.0)
    dissolver = subject.get("dissolution", 1.0)
    food = case["food"]

    pka_factor = clamp(0.70 + 0.08 * (case["pka"] - 5.0), 0.35, 1.30)
    logp_factor = clamp(0.70 + 0.11 * case["logP"], 0.35, 1.60)
    permeability_factor = clamp(case["permeability"] / 1.2, 0.18, 2.20)
    solubility_factor = clamp(case["solubility"] * form["sol"], 0.15, 4.00)
    particle_factor = clamp((1.0 / max(form["part"], 0.1)) ** 0.55, 0.35, 2.50)
    fabs = clamp(form["fabs"] * (0.74 + 0.18 * permeability_factor), 0.05, 0.995)
    gastric_drag = clamp(1.15 / food, 0.55, 1.50)
    kel = max(0.01, cl_value / max(volume, 1.0))
    hepatic_extraction = clamp(0.12 + cl_value / (cl_value + 40.0) + 0.03 * (1.0 - form["sol"]), 0.08, 0.72)

    solid = np.zeros(len(SEGMENTS), dtype=float)
    dissolved = np.zeros(len(SEGMENTS), dtype=float)
    solid[0] = case["dose"]
    tissues = {name: 0.0 for name in TISSUES}
    plasma_amt = 0.0
    absorbed_cum = 0.0
    dissolved_cum = 0.0

    series = {segment["key"]: [] for segment in SEGMENTS}
    for name in ["portal", "plasma", "liver", "heart", "kidney", "muscle", "fat", "vessel", "effect"]:
        series[name] = []
    fraction_dissolved: List[float] = []
    fraction_absorbed: List[float] = []

    for _ in times:
        incoming_solid = np.zeros(len(SEGMENTS), dtype=float)
        incoming_dissolved = np.zeros(len(SEGMENTS), dtype=float)
        step_portal_input = 0.0

        for idx, segment in enumerate(SEGMENTS):
            ph_factor = clamp(0.52 + 0.10 * segment["ph"], 0.55, 1.35)
            dissolve_rate = 0.18 * form["diss"] * solubility_factor * particle_factor * segment["agitation"] * gastric_drag * dissolver * ph_factor
            dissolve_amount = min(solid[idx], solid[idx] * dissolve_rate * dt)
            solid[idx] -= dissolve_amount
            dissolved[idx] += dissolve_amount
            dissolved_cum += dissolve_amount

            ion_factor = clamp(pka_factor * (0.88 + 0.05 * idx), 0.20, 1.55)
            absorb_rate = ka_base * permeability_factor * logp_factor * segment["surface"] * ion_factor
            absorb_amount = min(dissolved[idx], dissolved[idx] * absorb_rate * dt)
            dissolved[idx] -= absorb_amount
            systemic_input = absorb_amount * fabs
            step_portal_input += systemic_input
            absorbed_cum += systemic_input

            pool = solid[idx] + dissolved[idx]
            transit_rate = case["ktr"] * segment["transit"] * (0.78 + 0.04 * idx) / clamp(food, 0.55, 1.6)
            transit_amount = min(pool, pool * transit_rate * dt)
            if transit_amount > 0 and idx < len(SEGMENTS) - 1:
                solid_share = solid[idx] / pool if pool > 0 else 0.0
                dissolved_share = dissolved[idx] / pool if pool > 0 else 0.0
                solid[idx] -= transit_amount * solid_share
                dissolved[idx] -= transit_amount * dissolved_share
                incoming_solid[idx + 1] += transit_amount * solid_share
                incoming_dissolved[idx + 1] += transit_amount * dissolved_share

        solid += incoming_solid
        dissolved += incoming_dissolved

        liver_input = step_portal_input * hepatic_extraction
        plasma_input = step_portal_input * (1.0 - hepatic_extraction)
        tissues["liver"] += liver_input
        plasma_amt += plasma_input

        liver_release = tissues["liver"] * 0.18 * dt
        liver_clear = tissues["liver"] * kel * 0.20 * dt
        tissues["liver"] = max(0.0, tissues["liver"] - liver_release - liver_clear)
        plasma_amt += liver_release

        plasma_conc = plasma_amt / max(volume, 1.0)
        for name in ("heart", "kidney", "muscle", "fat", "vessel"):
            tissue = TISSUES[name]
            uptake = tissue["kin"] * plasma_conc * tissue["volume"] * dt
            release = tissue["kout"] * tissues[name] * dt
            delta = uptake - release
            tissues[name] = max(0.0, tissues[name] + delta)
            plasma_amt = max(0.0, plasma_amt - delta)

        plasma_amt = max(0.0, plasma_amt - plasma_amt * kel * dt)
        plasma_conc = plasma_amt / max(volume, 1.0)
        effect = case["e0"] + case["emax"] * ((plasma_conc ** case["hill"]) / ((case["ec50"] ** case["hill"]) + (plasma_conc ** case["hill"]) + 1e-9))

        for idx, segment in enumerate(SEGMENTS):
            series[segment["key"]].append(float(solid[idx] + dissolved[idx]))
        series["portal"].append(float(step_portal_input / max(dt, 1e-9)))
        series["plasma"].append(float(plasma_conc))
        series["liver"].append(float(tissues["liver"] / TISSUES["liver"]["volume"]))
        series["heart"].append(float(tissues["heart"] / TISSUES["heart"]["volume"]))
        series["kidney"].append(float(tissues["kidney"] / TISSUES["kidney"]["volume"]))
        series["muscle"].append(float(tissues["muscle"] / TISSUES["muscle"]["volume"]))
        series["fat"].append(float(tissues["fat"] / TISSUES["fat"]["volume"]))
        series["vessel"].append(float(tissues["vessel"] / TISSUES["vessel"]["volume"]))
        series["effect"].append(float(effect))
        fraction_dissolved.append(float(clamp(dissolved_cum / case["dose"], 0.0, 1.0)))
        fraction_absorbed.append(float(clamp(absorbed_cum / case["dose"], 0.0, 1.0)))

    plasma_series = np.asarray(series["plasma"], dtype=float)
    cmax = float(plasma_series.max())
    tmax = float(times[int(plasma_series.argmax())])
    auc = float(np.trapezoid(plasma_series, times))

    return {
        "times": times.tolist(),
        "series": series,
        "cmax": cmax,
        "tmax": tmax,
        "auc": auc,
        "fAbs": float(clamp(absorbed_cum / case["dose"], 0.0, 1.0)),
        "fractionDissolved": fraction_dissolved,
        "fractionAbsorbed": fraction_absorbed,
        "sampledDissolution": sample_profile(times, fraction_dissolved, PROFILE_TICKS),
        "sampledAbsorption": sample_profile(times, fraction_absorbed, PROFILE_TICKS),
        "sampledPlasma": sample_profile(times, series["plasma"], STUDY_TIMES),
        "sampledEffect": sample_profile(times, series["effect"], PROFILE_TICKS),
        "peakEffect": float(max(series["effect"])),
    }


def f2_factor(reference: List[float], test: List[float]) -> float:
    ref = np.asarray(reference[1:], dtype=float)
    tst = np.asarray(test[1:], dtype=float)
    mse = float(np.mean((ref - tst) ** 2))
    return float(50.0 * math.log10(100.0 / math.sqrt(1.0 + mse)))


def run_ivivc(case: Dict[str, Any], reference_profile: Dict[str, Any]) -> Dict[str, Any]:
    known = pd.DataFrame({"time": PROFILE_TICKS, "C": np.asarray(reference_profile["sampledDissolution"], dtype=float)})
    oral = pd.DataFrame({"time": PROFILE_TICKS, "C": np.interp(PROFILE_TICKS, np.asarray(reference_profile["times"], dtype=float), np.asarray(reference_profile["series"]["plasma"], dtype=float))})
    kel = case["cl"] / max(case["v"], 1.0)
    impulse_curve = (case["dose"] / max(case["v"], 1.0)) * np.exp(-kel * PROFILE_TICKS)
    impulse = pd.DataFrame({"time": PROFILE_TICKS, "C": impulse_curve})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        regression, numeric = pyivivc(known, impulse, oral, dose_iv=case["dose"], dose_po=case["dose"], explicit_interpolation=8, implicit_interpolation=6, maxit_optim=20)
    return {
        "slope": float(regression.slope),
        "intercept": float(regression.intercept),
        "r": float(regression.rvalue),
        "r2": float(regression.rvalue ** 2),
        "pvalue": float(regression.pvalue),
        "numeric": numeric.to_dict(orient="records"),
    }


def simulate_be_pair(case: Dict[str, Any], rng: np.random.Generator) -> Dict[str, Any]:
    subject_base = {
        "clearance": lognormal_mean_one(case["clcv"], rng),
        "volume": lognormal_mean_one(case["vcv"], rng),
        "absorption": lognormal_mean_one(case["kacv"], rng),
        "dissolution": lognormal_mean_one(max(6.0, case["kacv"] * 0.7), rng),
    }
    ref_period = {
        key: value * lognormal_mean_one(8.0, rng)
        for key, value in subject_base.items()
    }
    test_period = {
        key: value * lognormal_mean_one(8.0, rng)
        for key, value in subject_base.items()
    }
    return {
        "ref": simulate_profile(case, "Reference", ref_period),
        "test": simulate_profile(case, "Test", test_period),
    }


def manual_be_stats(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    auc_logs = np.array([math.log(pair["test"]["auc"] / pair["ref"]["auc"]) for pair in pairs], dtype=float)
    cmax_logs = np.array([math.log(pair["test"]["cmax"] / pair["ref"]["cmax"]) for pair in pairs], dtype=float)

    def ci(values: np.ndarray) -> Dict[str, float]:
        mean_val = float(values.mean())
        sd_val = float(values.std(ddof=1)) if len(values) > 1 else 0.0
        se = sd_val / math.sqrt(max(len(values), 1))
        t_value = t_critical_90(len(values) - 1)
        return {
            "gmr": float(math.exp(mean_val)),
            "lower": float(math.exp(mean_val - t_value * se)),
            "upper": float(math.exp(mean_val + t_value * se)),
        }

    auc_ci = ci(auc_logs)
    cmax_ci = ci(cmax_logs)
    passed = auc_ci["lower"] >= 0.8 and auc_ci["upper"] <= 1.25 and cmax_ci["lower"] >= 0.8 and cmax_ci["upper"] <= 1.25
    return {"auc": auc_ci, "cmax": cmax_ci, "pass": passed}


def build_parallel_dataframe(pairs: List[Dict[str, Any]]) -> pl.DataFrame:
    rows: List[Dict[str, Any]] = []
    half = max(3, len(pairs) // 2)
    for idx, pair in enumerate(pairs, start=1):
        for form, key in (("Reference", "ref"), ("Test", "test")):
            subject_id = f"{'R' if form == 'Reference' else 'T'}{idx:03d}"
            sampled = np.asarray(pair[key]["sampledPlasma"], dtype=float)
            for time_point, concentration in zip(STUDY_TIMES, sampled):
                rows.append({
                    "subject": subject_id,
                    "time": float(time_point),
                    "conc": float(concentration),
                    "form": form,
                })
    return pl.DataFrame(rows)


def analyze_be_with_bioeq(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    dataset = build_parallel_dataframe(pairs)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        analyzer = ParallelDesign(dataset, "subject", "time", "conc", "form")
        auc_stats = analyzer.calculate_point_estimate("log_AUC")
        cmax_stats = analyzer.calculate_point_estimate("log_Cmax")
        summary = analyzer.summarize_pk_parameters().to_dicts()
    return {
        "auc": {
            "point_estimate": float(auc_stats["point_estimate"] / 100.0),
            "lower_90ci": float(auc_stats["lower_90ci"] / 100.0),
            "upper_90ci": float(auc_stats["upper_90ci"] / 100.0),
            "pass": bool(auc_stats["be_criteria_met"]),
        },
        "cmax": {
            "point_estimate": float(cmax_stats["point_estimate"] / 100.0),
            "lower_90ci": float(cmax_stats["lower_90ci"] / 100.0),
            "upper_90ci": float(cmax_stats["upper_90ci"] / 100.0),
            "pass": bool(cmax_stats["be_criteria_met"]),
        },
        "pass": bool(auc_stats["be_criteria_met"] and cmax_stats["be_criteria_met"]),
        "summary": summary,
    }


def build_region_report(reference: Dict[str, Any], test: Dict[str, Any]) -> List[Dict[str, Any]]:
    times = np.asarray(reference["times"], dtype=float)
    region_names = [
        ("胃", "stomach"),
        ("十二指肠", "duodenum"),
        ("空肠", "jejunum"),
        ("回肠", "ileum"),
        ("结肠", "colon"),
        ("门静脉", "portal"),
        ("肝脏", "liver"),
        ("血浆", "plasma"),
        ("心脏", "heart"),
        ("肾脏", "kidney"),
        ("肌肉", "muscle"),
        ("脂肪", "fat"),
    ]
    rows = []
    for label, key in region_names:
        ref_values = np.asarray(reference["series"][key], dtype=float)
        test_values = np.asarray(test["series"][key], dtype=float)
        ref_cmax = float(ref_values.max())
        test_cmax = float(test_values.max())
        rows.append({
            "region": label,
            "reference_auc": float(np.trapezoid(ref_values, times)),
            "test_auc": float(np.trapezoid(test_values, times)),
            "reference_cmax": ref_cmax,
            "test_cmax": test_cmax,
            "reference_tmax": float(times[int(ref_values.argmax())]),
            "test_tmax": float(times[int(test_values.argmax())]),
        })
    return rows


def perform_self_check(force: bool = False) -> Dict[str, Any]:
    age = time.time() - SELF_CHECK_CACHE["at"]
    if not force and SELF_CHECK_CACHE["value"] is not None and age < 20:
        return SELF_CHECK_CACHE["value"]

    checks: List[Dict[str, Any]] = []
    checks.append({
        "name": "Python 运行时",
        "ok": True,
        "detail": os.sys.version.split()[0],
    })

    for package_name in PACKAGE_CHECKS:
        version = version_of(package_name)
        checks.append({
            "name": f"Python 包 {package_name}",
            "ok": version != "missing",
            "detail": version,
        })

    for repo_name, repo_path in REPO_CHECKS:
        checks.append({
            "name": repo_name,
            "ok": repo_path.exists(),
            "detail": str(repo_path.relative_to(ROOT)) if repo_path.exists() else "未下载到本地",
        })

    try:
        smoke_case = merge_case({**DEFAULT_CASE, "subjects": 6, "trials": 4})
        reference = simulate_profile(smoke_case, "Reference")
        test = simulate_profile(smoke_case, "Test")
        ivivc = run_ivivc(smoke_case, reference)
        f2_value = f2_factor(reference["sampledDissolution"], test["sampledDissolution"])
        checks.append({
            "name": "模拟引擎冒烟测试",
            "ok": math.isfinite(reference["auc"]) and math.isfinite(test["auc"]) and math.isfinite(f2_value),
            "detail": f"AUC {reference['auc']:.2f}/{test['auc']:.2f}, IVIVC r={ivivc['r']:.3f}",
        })
        backend_demo = {
            "f2": f2_value,
            "ivivc_r": ivivc["r"],
            "reference_auc": reference["auc"],
            "test_auc": test["auc"],
        }
    except Exception as exc:  # pragma: no cover - defensive path
        checks.append({
            "name": "模拟引擎冒烟测试",
            "ok": False,
            "detail": str(exc),
        })
        backend_demo = None

    result = {
        "ok": all(check["ok"] for check in checks),
        "checked_at": now_iso(),
        "checks": checks,
        "default_case": DEFAULT_CASE,
        "demo": backend_demo,
    }
    SELF_CHECK_CACHE["at"] = time.time()
    SELF_CHECK_CACHE["value"] = result
    return result


def execute_job(job_id: str, case: Dict[str, Any]) -> None:
    try:
        update_job(job_id, status="running", progress=0.02, phase="init", phase_label="初始化", message="开始校验参数和启动本地计算。")
        append_log(job_id, "后端任务已启动，当前为真实 Python 计算。")
        case = merge_case(case)

        update_job(job_id, progress=0.08, phase="reference", phase_label="参考制剂", message="正在计算参考制剂体内溶解、转运和分布。")
        append_log(job_id, "开始参考制剂吸收/溶解模拟。")
        reference = simulate_profile(case, "Reference")

        update_job(job_id, progress=0.22, phase="test", phase_label="测试制剂", message="正在计算测试制剂体内溶解、转运和分布。")
        append_log(job_id, "开始测试制剂吸收/溶解模拟。")
        test = simulate_profile(case, "Test")

        update_job(job_id, progress=0.36, phase="ivivc", phase_label="IVIVC / f2", message="正在计算溶出相似性与体内外相关性。")
        append_log(job_id, "开始 IVIVC 与 f2 计算。")
        f2_value = f2_factor(reference["sampledDissolution"], test["sampledDissolution"])
        ivivc = run_ivivc(case, reference)

        update_job(job_id, progress=0.48, phase="be_study", phase_label="BE 单次试验", message="正在生成单次虚拟试验并调用 bioeq 统计。")
        append_log(job_id, "开始调用 bioeq 计算单次 BE 统计。")
        rng = np.random.default_rng(abs(hash(json.dumps(case, sort_keys=True))) % (2 ** 32))
        study_pairs = [simulate_be_pair(case, rng) for _ in range(case["subjects"])]
        bioeq_stats = analyze_be_with_bioeq(study_pairs)
        manual_study = manual_be_stats(study_pairs)

        update_job(job_id, progress=0.60, phase="be_monte_carlo", phase_label="BE 统计中", message="正在做虚拟受试者重复试验和 90% CI 统计。")
        append_log(job_id, f"开始 Monte Carlo，{case['trials']} 轮虚拟 BE。")
        pass_count = 0
        trial_curve: List[Dict[str, Any]] = []
        trial_results: List[Dict[str, Any]] = []
        step_interval = max(1, case["trials"] // 12)
        for trial_index in range(case["trials"]):
            trial_pairs = [simulate_be_pair(case, rng) for _ in range(case["subjects"])]
            trial_stat = manual_be_stats(trial_pairs)
            pass_count += 1 if trial_stat["pass"] else 0
            trial_results.append(trial_stat)
            trial_curve.append({
                "trial": trial_index + 1,
                "passRate": pass_count / float(trial_index + 1),
            })
            if trial_index == 0 or (trial_index + 1) % step_interval == 0 or trial_index + 1 == case["trials"]:
                progress = 0.60 + 0.32 * ((trial_index + 1) / case["trials"])
                update_job(job_id, progress=progress, phase="be_monte_carlo", phase_label="BE 统计中", message=f"已完成 {trial_index + 1}/{case['trials']} 轮虚拟 BE。")
                append_log(job_id, f"虚拟 BE 进度 {trial_index + 1}/{case['trials']}。")

        pass_rate = pass_count / float(case["trials"])
        region_report = build_region_report(reference, test)

        update_job(job_id, progress=0.96, phase="packaging", phase_label="整理结果", message="正在汇总报表、日志和图表数据。")
        append_log(job_id, "整理最终结果。")
        elapsed = time.time() - JOBS[job_id]["started_at"] if JOBS[job_id]["started_at"] else 0.0
        result = {
            "params": case,
            "reference": reference,
            "test": test,
            "f2": f2_value,
            "ivivc": ivivc,
            "study": {
                "bioeq": bioeq_stats,
                "manual": manual_study,
                "passRate": pass_rate,
                "trialCurve": trial_curve,
                "trialResults": trial_results[: min(len(trial_results), 12)],
            },
            "regionReport": region_report,
            "runtimeSeconds": round(elapsed, 2),
            "openSource": {
                "repos": [{"name": name, "present": path.exists(), "path": str(path.relative_to(ROOT))} for name, path in REPO_CHECKS],
                "packages": [{"name": name, "version": version_of(name)} for name in PACKAGE_CHECKS],
            },
        }
        update_job(job_id, status="completed", progress=1.0, phase="completed", phase_label="已完成", message="真实后端计算已完成，结果已可展示。", result=serialise(result))
        append_log(job_id, f"任务完成，耗时 {elapsed:.2f}s，BE 成功率 {pass_rate:.1%}。")
    except Exception as exc:  # pragma: no cover - defensive path
        traceback_text = traceback.format_exc()
        update_job(job_id, status="failed", progress=1.0, phase="failed", phase_label="失败", message="后端任务执行失败。", error=f"{exc}\n{traceback_text}")
        append_log(job_id, f"任务失败：{exc}")


@app.get("/api/health")
def api_health() -> JSONResponse:
    return JSONResponse({"ok": True, "time": now_iso()})


@app.get("/api/self-check")
def api_self_check() -> JSONResponse:
    return JSONResponse(serialise(perform_self_check()))


@app.post("/api/run")
def api_run(case_input: CaseInput) -> JSONResponse:
    case = merge_case(case_input.model_dump())
    job = create_job(case)
    worker = threading.Thread(target=execute_job, args=(job["id"], case), daemon=True)
    worker.start()
    return JSONResponse({
        "job_id": job["id"],
        "status": job["status"],
        "eta_seconds": job["eta_seconds"],
    })


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> JSONResponse:
    try:
        return JSONResponse(job_snapshot(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.get("/")
def root_index() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/{asset_name}")
def asset(asset_name: str) -> FileResponse:
    safe_files = {"index.html", "app.js", "styles.css", "README.md"}
    if asset_name not in safe_files:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(ROOT / asset_name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    if args.open:
        def _open_browser() -> None:
            time.sleep(1.2)
            webbrowser.open(f"http://{HOST}:{PORT}")
        threading.Thread(target=_open_browser, daemon=True).start()

    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
