"""The semantic layer as tools: MetricFlow over the dbt project, called from Python."""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import tempfile
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any, Sequence

from metricflow.engine.metricflow_engine import MetricFlowEngine, MetricFlowQueryRequest
from metricflow_semantics.model.semantic_manifest_lookup import SemanticManifestLookup

from dbt_metricflow.cli.dbt_connectors.adapter_backed_client import AdapterBackedSqlClient
from dbt_metricflow.cli.dbt_connectors.dbt_config_accessor import dbtArtifacts, dbtProjectMetadata

from .data import DBT_DIR, RAW_DIR, SEMANTIC_DB

# Dimensions that only exist to define metrics; listing them would invite the model to re-derive the filters.
HIDDEN = {"is_valid", "is_canceled", "is_delivered"}


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()[:10]
    return value


class SemanticLayer:
    @cached_property
    def engine(self) -> MetricFlowEngine:
        # dbt opens DuckDB for writing, which locks the file; each process queries its own copy, so several
        # benchmark runs can share the machine.
        copy = Path(tempfile.mkdtemp(prefix="metricbench-")) / SEMANTIC_DB.name
        shutil.copyfile(SEMANTIC_DB, copy)
        os.environ.setdefault("METRICBENCH_RAW_DIR", str(RAW_DIR))
        os.environ["METRICBENCH_DB"] = str(copy)
        metadata = dbtProjectMetadata.load_from_paths(profiles_path=DBT_DIR, project_path=DBT_DIR)
        artifacts = dbtArtifacts.load_from_project_metadata(metadata)
        logging.getLogger("metricflow").setLevel(logging.WARNING)
        return MetricFlowEngine(
            semantic_manifest_lookup=SemanticManifestLookup(artifacts.semantic_manifest),
            sql_client=AdapterBackedSqlClient(artifacts.adapter),
        )

    def list_metrics(self) -> list[dict[str, str]]:
        return [
            {"name": m.name, "description": m.description or "", "type": m.type.value}
            for m in sorted(self.engine.list_metrics(include_dimensions=False), key=lambda m: m.name)
        ]

    def list_dimensions(self, metrics: Sequence[str]) -> list[dict[str, str]]:
        """Dimensions that every one of `metrics` can be grouped or filtered by."""
        dims = self.engine.simple_dimensions_for_metrics(list(metrics))
        return [
            {"name": d.dunder_name, "description": d.description or "", "type": d.type.value}
            for d in sorted(dims, key=lambda d: d.dunder_name)
            if d.name not in HIDDEN
        ]

    def query(
        self,
        metrics: Sequence[str],
        group_by: Sequence[str] = (),
        where: Sequence[str] = (),
        order_by: Sequence[str] = (),
        limit: int | None = None,
    ) -> dict[str, Any]:
        result = self.engine.query(
            MetricFlowQueryRequest.create(
                metric_names=list(metrics),
                group_by_names=list(group_by) or None,
                where_constraints=list(where) or None,
                order_by_names=list(order_by) or None,
                limit=limit,
            )
        )
        table = result.result_df
        assert table is not None
        return {
            "columns": list(table.column_names),
            "rows": [[_plain(v) for v in row] for row in table.rows],
            "sql": result.sql,
        }
