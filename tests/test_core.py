"""Unit tests for the shared core: configuration, SQL splitting, routing."""

from __future__ import annotations

import os

import pytest

from nexgen.core import config as config_mod
from nexgen.core.migrate import split_sql
from nexgen.supervisor.routes import build_table, resolve


# -- configuration ----------------------------------------------------------

def test_placeholder_with_default_is_retyped(monkeypatch):
    monkeypatch.delenv("NX_TEST_PORT", raising=False)
    assert config_mod._expand("${NX_TEST_PORT:-3306}") == 3306
    assert config_mod._expand("${NX_TEST_RATE:-1.5}") == 1.5
    assert config_mod._expand("${NX_TEST_FLAG:-true}") is True


def test_placeholder_reads_environment(monkeypatch):
    monkeypatch.setenv("NX_TEST_HOST", "db.internal")
    assert config_mod._expand("${NX_TEST_HOST:-localhost}") == "db.internal"


def test_embedded_placeholder_stays_text(monkeypatch):
    monkeypatch.setenv("NX_TEST_DIR", "C:/Program Files/MySQL")
    assert config_mod._expand("${NX_TEST_DIR}/bin") == "C:/Program Files/MySQL/bin"


def test_secret_with_colon_is_not_parsed_as_yaml(monkeypatch):
    monkeypatch.setenv("NX_TEST_SECRET", "a: b # c")
    assert config_mod._expand("${NX_TEST_SECRET}") == "a: b # c"


def test_mask_hides_secrets_only():
    masked = config_mod.mask({"user": "root", "password": "x", "tms": {"auth_key": "k", "base_url": "u"}})
    assert masked == {"user": "root", "password": "********", "tms": {"auth_key": "********", "base_url": "u"}}


def test_repository_config_loads_and_validates():
    cfg = config_mod.Config()
    assert set(cfg.service_names) >= {"platform", "ingestion", "fleet", "geofence", "routing", "analytics", "ml"}
    ports = [cfg.service(n).port for n in cfg.service_names]
    assert len(ports) == len(set(ports))
    assert cfg.gateway_port not in ports
    assert cfg.tenant_id() == 1
    # tenant files inherit the defaults
    assert cfg.tenant()["thresholds"]["overspeed_kmph"] == 60


def test_duplicate_ports_are_refused(tmp_path):
    (tmp_path / "tenants").mkdir()
    (tmp_path / "tenants" / "_default.yaml").write_text("modules: {}\n")
    (tmp_path / "tenants" / "a.yaml").write_text("tenant: {id: 1, code: a}\n")
    (tmp_path / "database.yaml").write_text("schemas: {events: e, platform: p, fleet: f}\n")
    (tmp_path / "services.yaml").write_text(
        "run: {gateway_port: 9000}\n"
        "services:\n  a: {port: 9001, roles: {api: {}}}\n  b: {port: 9001, roles: {api: {}}}\n")
    with pytest.raises(ValueError, match="also used by"):
        config_mod.Config(tmp_path)


# -- SQL splitting ----------------------------------------------------------

def test_split_ignores_semicolons_in_comments_and_strings():
    sql = """
    -- a comment; with a semicolon
    CREATE TABLE t (a INT COMMENT 'x;y');  # another; comment
    /* block; comment */
    INSERT INTO t VALUES (1);
    SELECT "a;b", `c;d` FROM t
    """
    stmts = split_sql(sql)
    assert len(stmts) == 3
    assert stmts[0].startswith("CREATE TABLE t") and "'x;y'" in stmts[0]
    assert stmts[2].startswith('SELECT "a;b"')


def test_split_handles_doubled_and_escaped_quotes():
    stmts = split_sql("INSERT INTO t VALUES ('it''s; fine'); INSERT INTO t VALUES ('a\\';b');")
    assert len(stmts) == 2


# -- gateway routing --------------------------------------------------------

def test_most_specific_route_wins():
    table = build_table()
    assert resolve(table, "/api/v1/tta/trips/123/weather").service == "analytics"
    assert resolve(table, "/api/v1/tta/trips/123").service == "analytics"
    assert resolve(table, "/api/v1/tta/sync/status").service == "ingestion"
    assert resolve(table, "/api/v1/tta/upload").service == "ingestion"
    assert resolve(table, "/api/v1/tta/maintenance/status").service == "platform"
    assert resolve(table, "/api/v1/geo/routes").service == "routing"
    assert resolve(table, "/api/v1/geo/trips/5").service == "geofence"
    assert resolve(table, "/api/v1/geofence/fences").service == "geofence"
    assert resolve(table, "/api/v1/dashboard/kpis").service == "analytics"
    assert resolve(table, "/api/v1/fleet/trips").service == "fleet"
    assert resolve(table, "/api/v1/ml/models").service == "ml"
    assert resolve(table, "/api/v1/nowhere") is None
