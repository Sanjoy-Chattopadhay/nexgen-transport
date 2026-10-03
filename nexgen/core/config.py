"""One loader for every setting.

NexGen Transport keeps its settings in three places, each for one kind of
thing, and nowhere else:

    config/services.yaml   every service, role, route, schedule, external API
                           and engine setting
    config/database.yaml   every database connection
    config/tenants/*.yaml  per-client business settings over _default.yaml
    .env                   secrets only, referenced from the YAML as ${NAME}

Both legacy applications read settings straight from the environment in
whichever module needed them (Smart-Truck's config/settings.py, Geo-Fencing's
config.py dataclasses). That made "what is this system configured to do"
unanswerable without reading code, and two processes could disagree. Here every
service asks this module, the developer page can show the effective values
(secrets masked), and a run can record exactly what it was configured with.

Substitution: `${NAME}` is replaced by the environment variable, `${NAME:-x}`
falls back to `x` when it is unset or empty. A value that is exactly one
placeholder is re-typed after substitution ("3306" -> 3306, "true" -> True),
so `port: "${DB_PORT:-3306}"` is an int. Values that merely contain a
placeholder stay strings.
"""

from __future__ import annotations

import copy
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(os.environ.get("NEXGEN_CONFIG_DIR", ROOT / "config"))

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")
_INT = re.compile(r"^-?\d+$")
_FLOAT = re.compile(r"^-?\d+\.\d*$|^-?\d*\.\d+$")
_SECRET_KEYS = re.compile(r"(password|secret|auth_key|api_key|api_keys|token)$", re.I)


def _retype(text: str) -> Any:
    """'3306' -> 3306, '1.5' -> 1.5, 'true' -> True. Anything else unchanged."""
    s = text.strip()
    if _INT.match(s):
        return int(s)
    if _FLOAT.match(s):
        return float(s)
    if s.lower() in ("true", "yes", "on"):
        return True
    if s.lower() in ("false", "no", "off"):
        return False
    return text


def _expand(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if not isinstance(value, str) or "${" not in value:
        return value

    def sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        got = os.environ.get(name)
        if got is None or got == "":
            return default if default is not None else ""
        return got

    whole = _PLACEHOLDER.fullmatch(value.strip()) is not None
    out = _PLACEHOLDER.sub(sub, value)
    return _retype(out) if whole else out


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def mask(value: Any, key: str = "") -> Any:
    """A copy with every secret-looking value replaced, for display."""
    if isinstance(value, dict):
        return {k: mask(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask(v, key) for v in value]
    if key and _SECRET_KEYS.search(key) and value not in (None, "", 0):
        return "********"
    return value


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"missing configuration file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must hold a mapping at the top level")
    return data


@dataclass(frozen=True)
class RoleSpec:
    name: str
    autostart: bool
    description: str


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    title: str
    description: str
    port: int
    roles: tuple[RoleSpec, ...]
    routes: tuple[str, ...]
    settings: dict

    def role(self, name: str) -> RoleSpec | None:
        return next((r for r in self.roles if r.name == name), None)


class Config:
    """The parsed configuration. Cheap to read; reload() re-reads the files."""

    def __init__(self, config_dir: Path = CONFIG_DIR):
        self.dir = Path(config_dir)
        self._lock = threading.Lock()
        self.reload()

    # -- loading -------------------------------------------------------------
    def reload(self) -> None:
        load_dotenv(ROOT / ".env", override=False)
        services = _expand(_read_yaml(self.dir / "services.yaml"))
        database = _expand(_read_yaml(self.dir / "database.yaml"))
        tenants: dict[str, dict] = {}
        tdir = self.dir / "tenants"
        default = _expand(_read_yaml(tdir / "_default.yaml")) if (tdir / "_default.yaml").exists() else {}
        for path in sorted(tdir.glob("*.yaml")) if tdir.exists() else []:
            if path.name.startswith("_"):
                continue
            own = _expand(_read_yaml(path))
            merged = _deep_merge(default, own)
            code = (own.get("tenant") or {}).get("code") or path.stem
            merged.setdefault("tenant", {})["code"] = code
            tenants[code] = merged
        self._validate(services, database, tenants)
        with self._lock:
            self.services_raw = services
            self.database_raw = database
            self.tenant_defaults = default
            self.tenants = tenants
            self._specs = self._build_specs(services)

    @staticmethod
    def _validate(services: dict, database: dict, tenants: dict) -> None:
        problems: list[str] = []
        ports: dict[int, str] = {}
        for name, svc in (services.get("services") or {}).items():
            port = svc.get("port")
            if not isinstance(port, int):
                problems.append(f"services.{name}.port must be an integer")
            elif port in ports:
                problems.append(f"services.{name}.port {port} is also used by {ports[port]}")
            else:
                ports[port] = name
            if not svc.get("roles"):
                problems.append(f"services.{name} declares no roles")
        gw = (services.get("run") or {}).get("gateway_port")
        if gw in ports:
            problems.append(f"run.gateway_port {gw} is also used by services.{ports[gw]}")
        for key in ("events", "platform", "fleet"):
            if key not in (database.get("schemas") or {}):
                problems.append(f"database.yaml schemas must name '{key}'")
        ids = {}
        for code, t in tenants.items():
            tid = (t.get("tenant") or {}).get("id")
            if not isinstance(tid, int) or tid <= 0:
                problems.append(f"tenant {code} needs a positive integer tenant.id")
            elif tid in ids:
                problems.append(f"tenant {code} reuses id {tid} of {ids[tid]}")
            else:
                ids[tid] = code
        if problems:
            raise ValueError("configuration problems:\n  " + "\n  ".join(problems))

    @staticmethod
    def _build_specs(services: dict) -> dict[str, ServiceSpec]:
        specs = {}
        for name, svc in (services.get("services") or {}).items():
            roles = tuple(
                RoleSpec(r, bool((v or {}).get("autostart", True)), str((v or {}).get("description", "")))
                for r, v in (svc.get("roles") or {}).items()
            )
            specs[name] = ServiceSpec(
                name=name,
                title=str(svc.get("title") or name.title()),
                description=str(svc.get("description") or ""),
                port=int(svc["port"]),
                roles=roles,
                routes=tuple(svc.get("routes") or ()),
                settings=svc.get("settings") or {},
            )
        return specs

    # -- services ------------------------------------------------------------
    @property
    def service_names(self) -> list[str]:
        return list(self._specs)

    def service(self, name: str) -> ServiceSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(f"no service named {name!r} in services.yaml") from None

    def get(self, path: str, default: Any = None) -> Any:
        """Dotted lookup in services.yaml: get('integrations.tms.base_url')."""
        node: Any = self.services_raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def setting(self, service: str, path: str, default: Any = None) -> Any:
        """Dotted lookup inside one service's `settings` block."""
        return self.get(f"services.{service}.settings.{path}", default)

    @property
    def product(self) -> dict:
        return self.services_raw.get("product") or {}

    @property
    def run(self) -> dict:
        return self.services_raw.get("run") or {}

    @property
    def gateway_port(self) -> int:
        return int(self.run.get("gateway_port", 8100))

    @property
    def host(self) -> str:
        return str(self.run.get("host", "127.0.0.1"))

    def path(self, value: str | Path) -> Path:
        """A configured path, relative to the repository root unless absolute."""
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    # -- database ------------------------------------------------------------
    def schema(self, key: str) -> str:
        schemas = self.database_raw.get("schemas") or {}
        if key not in schemas:
            raise KeyError(f"database.yaml names no schema '{key}'")
        return str(schemas[key])

    @property
    def schema_keys(self) -> list[str]:
        return list((self.database_raw.get("schemas") or {}).keys())

    def legacy_database(self, name: str) -> str:
        legacy = self.database_raw.get("legacy") or {}
        if name not in legacy:
            raise KeyError(f"database.yaml names no legacy source '{name}'")
        return str(legacy[name]["database"])

    def server(self, name: str = "primary") -> dict:
        return (self.database_raw.get("servers") or {})[name]

    def credentials(self, schema_key: str) -> dict:
        creds = self.database_raw.get("credentials") or {}
        return creds.get(schema_key) or creds.get("default") or {}

    # -- tenants -------------------------------------------------------------
    @property
    def default_tenant(self) -> str:
        return str(self.product.get("default_tenant") or next(iter(self.tenants), ""))

    def tenant(self, code: str | None = None) -> dict:
        code = code or self.default_tenant
        if code not in self.tenants:
            raise KeyError(f"no tenant '{code}' in config/tenants")
        return self.tenants[code]

    def tenant_id(self, code: str | None = None) -> int:
        return int(self.tenant(code)["tenant"]["id"])

    def tenant_by_id(self, tenant_id: int) -> dict:
        for t in self.tenants.values():
            if int(t["tenant"]["id"]) == int(tenant_id):
                return t
        raise KeyError(f"no tenant with id {tenant_id}")

    # -- display -------------------------------------------------------------
    def masked(self) -> dict:
        return {
            "services": mask(self.services_raw),
            "database": mask(self.database_raw),
            "tenants": mask(self.tenants),
        }


_config: Config | None = None
_config_lock = threading.Lock()


def get_config() -> Config:
    global _config
    if _config is None:
        with _config_lock:
            if _config is None:
                _config = Config()
    return _config


def reload_config() -> Config:
    cfg = get_config()
    cfg.reload()
    return cfg
