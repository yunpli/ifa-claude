"""Stock Edge replay/backtest primitives."""

from .global_preset import GlobalPresetPlan, plan_global_preset_refresh
from .labels import ForwardLabels, compute_forward_labels
from .objectives import HorizonObjectiveInputs, PredictionObjectiveInputs, continuous_overlay_bounds, score_horizon_objective, score_prediction_objective
from .optimizer import fit_global_preset, fit_pre_report_overlay
from .pre_report_tuning import PreReportTuningPlan, plan_pre_report_tuning
from .promotion import PromotionPatch, apply_promotion_patch, build_promotion_patch, emit_promotion_patch
from .report_runtime import ReportTuningResult, prepare_report_params
from .tuning_artifact import TuningArtifact, find_latest_tuning_artifact, read_tuning_artifact, write_tuning_artifact

__all__ = [
    "ForwardLabels",
    "GlobalPresetPlan",
    "HorizonObjectiveInputs",
    "PredictionObjectiveInputs",
    "PreReportTuningPlan",
    "ReportTuningResult",
    "PromotionPatch",
    "TuningArtifact",
    "continuous_overlay_bounds",
    "compute_forward_labels",
    "find_latest_tuning_artifact",
    "fit_global_preset",
    "fit_pre_report_overlay",
    "plan_global_preset_refresh",
    "plan_pre_report_tuning",
    "prepare_report_params",
    "apply_promotion_patch",
    "build_promotion_patch",
    "emit_promotion_patch",
    "read_tuning_artifact",
    "score_prediction_objective",
    "score_horizon_objective",
    "write_tuning_artifact",
]
