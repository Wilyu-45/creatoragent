"""校验容器化清单与代码的一致性（可离线跑，不需要 Docker daemon）。

**为什么需要这个检查**：Dockerfile 的 COPY 路径或 compose 的环境变量一旦写错，
只有真正 `docker build` 时才会暴露 —— 而 CI/开发机上经常没有 daemon。
把「清单里引用的东西是否存在」变成静态检查，就能在没有 daemon 的环境里提前拦住。

检查项：
1. Dockerfile 的每个 ``COPY`` / ``ADD`` 源路径在构建上下文中存在（且未被 .dockerignore 排除）
2. Dockerfile 声明的 ``ENV`` 变量名是后端真实读取的配置项
3. docker-compose 传递的环境变量名同样有效
4. k8s 清单里的 ConfigMap / Secret 键同样有效
5. compose 与 k8s 的探针路径确实是免鉴权端点（否则开了令牌容器永远不健康）
6. k8s 必须 ``replicas: 1`` 且发布策略为 ``Recreate`` —— 共享黑板 / 检查点 / 记忆库
   都在本地 JSON + SQLite，多副本或滚动更新会状态分裂；这条约束此前只写在清单注释里，
   改动者不一定看注释，所以升级为断言
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
COMPOSE = ROOT / "docker-compose.yml"
K8S = ROOT / "deploy" / "k8s.yaml"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'FAIL'} {label}" + (f"：{detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def known_env_vars() -> set[str]:
    """后端真实读取的环境变量名（直接从源码里扫 ``os.environ.get``）。"""
    names: set[str] = set()
    pattern = re.compile(r"os\.environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']")
    for path in (ROOT / "app").rglob("*.py"):
        names.update(pattern.findall(path.read_text(encoding="utf-8")))
    return names


def ignored_patterns() -> list[str]:
    if not DOCKERIGNORE.exists():
        return []
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _compose_service_block(compose_text: str, service: str) -> str:
    """精确截取 ``services.<service>`` 的整块文本（按缩进边界）。

    此前用 ``split("jaeger:", 1)`` 粗暴截断 —— 应用环境变量里一出现
    ``http://jaeger:4318`` 就会把其后半段环境变量误丢；引入 postgres/redis
    服务后，它们的变量也可能被误当作 app 的来校验。按缩进切块一劳永逸。
    """
    lines = compose_text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == f"  {service}:":
            start = i + 1
            break
    if start is None:
        return ""
    body: list[str] = []
    for line in lines[start:]:
        # 非空且缩进不足 4 格 = 下一个服务（2 格）或顶层键（0 格），本服务块结束
        if line.strip() and not line.startswith("    "):
            break
        body.append(line)
    return "\n".join(body)


def main() -> int:
    known = known_env_vars()
    ignored = ignored_patterns()

    print("[1] Dockerfile 的 COPY/ADD 源路径")
    text = DOCKERFILE.read_text(encoding="utf-8")
    # 逐条解析（含 --from=xxx 的多阶段拷贝，其源路径在另一阶段，跳过存在性检查）
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith(("COPY ", "ADD ")):
            continue
        tokens = stripped.split()[1:]
        # 过滤掉 --from=... 之类的选项
        from_stage = any(token.startswith("--from=") for token in tokens)
        paths = [token for token in tokens if not token.startswith("--")]
        if len(paths) < 2:
            continue
        sources, dest = paths[:-1], paths[-1]
        if from_stage:
            print(f"  SKIP {stripped}（来自其它构建阶段）")
            continue
        for source in sources:
            # 允许通配符：用 glob 判断是否有匹配
            if "*" in source or "?" in source:
                matched = list(ROOT.glob(source))
                check(f"{source} → {dest}", bool(matched), "通配符无匹配文件")
                continue
            target = ROOT / source
            excluded = next(
                (
                    pattern
                    for pattern in ignored
                    if not pattern.startswith("!")
                    and (
                        source == pattern.rstrip("/")
                        or source.startswith(pattern.rstrip("/") + "/")
                    )
                ),
                "",
            )
            detail = ""
            if not target.exists():
                detail = "构建上下文中不存在该路径"
            elif excluded:
                detail = f"被 .dockerignore 排除（{excluded}）"
            check(f"{source} → {dest}", target.exists() and not excluded, detail)

    print("\n[2] Dockerfile ENV 变量是否被后端读取")
    runtime_vars = {
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONNOUSERSITE",
        "PIP_NO_CACHE_DIR",
    }
    # Dockerfile 的 ENV 有两种写法：单行 `ENV A=1 B=2`，以及反斜杠续行
    # （`ENV A=1 \` + 缩进的 `B=2`）。这里把续行拼成一条逻辑语句再解析，
    # 否则续行里的变量会被漏检 —— 而它们恰恰是最容易拼错的那些。
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    for match in re.finditer(r"^\s*ENV\s+(.+)$", joined, re.MULTILINE):
        declaration = match.group(1).strip()
        for assignment in re.finditer(r"([A-Z0-9_]+)=", declaration):
            name = assignment.group(1)
            if name in runtime_vars:
                print(f"  SKIP {name}（Python/pip 运行时变量）")
                continue
            check(f"ENV {name}", name in known, "后端从未读取该变量（可能是拼写错误）")

    print("\n[3] docker-compose 的环境变量")
    compose_text = COMPOSE.read_text(encoding="utf-8")
    app_section = _compose_service_block(compose_text, "app")
    if not app_section:
        print("  FAIL 未能从 docker-compose.yml 解析出 services.app 块")
        failures.append("compose app 服务块解析")
    for match in re.finditer(r"^\s{6}([A-Z0-9_]+):", app_section, re.MULTILINE):
        name = match.group(1)
        check(f"compose {name}", name in known, "后端从未读取该变量")

    print("\n[4] k8s ConfigMap / Secret 的键")
    k8s_text = K8S.read_text(encoding="utf-8")
    for block_name in ("data:", "stringData:"):
        for section in re.split(r"\n---", k8s_text):
            is_config = "ConfigMap" in section
            is_secret = "Secret" in section
            if block_name not in section or not (is_config or is_secret):
                continue
            body = section.split(block_name, 1)[1]
            for match in re.finditer(r'^\s{2}([A-Z0-9_]+):', body, re.MULTILINE):
                name = match.group(1)
                check(f"k8s {name}", name in known, "后端从未读取该变量")

    print("\n[5] 探针路径必须免鉴权")
    # server.py 里显式放行的免鉴权路径
    server = (ROOT / "app" / "server.py").read_text(encoding="utf-8")
    probe_path = "/api/health"
    check(
        f"{probe_path} 免鉴权",
        f'path != "{probe_path}"' in server or f"'{probe_path}'" in server,
        "探针路径未在鉴权中间件中放行，开启令牌后容器将永远不健康",
    )
    check(
        "compose healthcheck 用该路径",
        probe_path in compose_text,
        "compose 探针路径与免鉴权路径不一致",
    )
    check(
        "k8s 探针用该路径",
        probe_path in k8s_text,
        "k8s 探针路径与免鉴权路径不一致",
    )

    print("\n[6] k8s 单副本约束（当前存储层不支持多副本）")
    # 只截取 Deployment 这一段，避免匹配到后面 Service 的 spec
    deployment = k8s_text.split("kind: Deployment", 1)[1].split("\nkind:", 1)[0]
    replicas = re.search(r"replicas:\s*(\d+)", deployment)
    check(
        "k8s replicas: 1",
        bool(replicas) and replicas.group(1) == "1",
        "本地 JSON + SQLite 存储不支持多副本，必须 replicas: 1；横向扩展需先换 PostgreSQL + Redis",
    )
    strategy = re.search(r"strategy:\s*\n\s*type:\s*(\w+)", deployment)
    check(
        "k8s strategy: Recreate",
        bool(strategy) and strategy.group(1) == "Recreate",
        "滚动更新会出现新旧副本并存，必须用 Recreate 避免状态分裂",
    )

    print()
    if failures:
        print(f"容器化清单核验未通过：{len(failures)} 项 → {'、'.join(failures)}")
        return 1
    print("容器化清单核验通过：COPY 路径、环境变量、探针、单副本约束均与代码一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
