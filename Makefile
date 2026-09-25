# Creator Agent Studio —— 构建 / 质量门禁 / 发行版 一键入口
#
# 快速上手（Windows 需先有 GNU make；根目录 make.bat 会自动找 conda env 里的 make）：
#   make help            目标清单
#   make verify          全量质量门禁（构建 + 6 道关卡，与 CI 同序）
#   make release         发行版前自检：verify 全绿 + 版本摘要（按约定不打包）
#   make tag             打 git 标签 v<VERSION>（发行版标记，需要干净工作区）
#
# 变量覆盖：make verify PYTHON="C:/path/python.exe" NPM=npm N=8 C=4
#
# 门禁约定：除 run / dev 外的所有目标自动注入 LLM_PROVIDER=mock（与 CI 一致，
# 不烧真实 token）；make run / dev 走项目 .env 的真实模型配置。
# verify/release 内部用 $(MAKE) 串行递归，即使带 -j 也不会并发跑门禁。

CONDA_PY := C:/Users/32525/.conda/envs/multi-agent-creator/python.exe
PYTHON ?= $(if $(wildcard $(CONDA_PY)),$(CONDA_PY),python)
NPM ?= npm
N ?= 8
C ?= 4

ifneq ($(wildcard VERSION),)
VERSION := $(strip $(file <VERSION))
else
VERSION := 0.1.0
endif

# 仅 run / dev 走 .env 真实模型；其余目标（含默认 help）注入离线引擎
ifneq ($(strip $(MAKECMDGOALS)),)
ifeq ($(strip $(filter-out run dev,$(MAKECMDGOALS))),)
else
export LLM_PROVIDER := mock
endif
endif
export PYTHONIOENCODING := utf-8

.PHONY: help install compile typecheck build run dev doctor golden contracts \
        deploy-check smoke stress verify release tag clean

help:
	@echo Creator Agent Studio - make targets
	@echo   install        pip and npm dependencies
	@echo   compile        byte-compile python sources - syntax gate
	@echo   typecheck      frontend type check with tsc
	@echo   build          build frontend bundle to dist
	@echo   run            start server via .env config - real provider
	@echo   dev            backend plus vite hot-reload dev mode
	@echo   doctor         environment and capability self-check
	@echo   golden         golden dataset quality regression
	@echo   contracts      service-level contract verification
	@echo   deploy-check   deployment manifest check - no docker needed
	@echo   smoke          end-to-end API acceptance
	@echo   stress         concurrency baseline with -n $(N) -c $(C)
	@echo   verify         ALL quality gates in CI order
	@echo   release        release gate - verify then version summary
	@echo   tag            create git tag v$(VERSION)
	@echo   clean          remove dist and __pycache__ caches

install:
	$(PYTHON) -m pip install -r requirements.txt
	$(NPM) ci

compile:
	$(PYTHON) -m compileall -q app scripts
	@echo compile ok

typecheck:
	$(NPM) run typecheck

build:
	$(NPM) run build

run:
	$(PYTHON) -m app.main

dev:
	$(NPM) run dev

doctor:
	$(PYTHON) scripts/doctor.py

golden:
	$(PYTHON) scripts/golden_eval.py

contracts:
	$(PYTHON) scripts/verify_contracts.py

deploy-check:
	$(PYTHON) scripts/check_deploy.py

smoke:
	$(PYTHON) scripts/smoke_api.py

stress:
	$(PYTHON) scripts/stress_llm.py -n $(N) -c $(C)

verify:
	$(MAKE) compile
	$(MAKE) typecheck
	$(MAKE) build
	$(MAKE) doctor
	$(MAKE) golden
	$(MAKE) contracts
	$(MAKE) deploy-check
	$(MAKE) smoke
	$(MAKE) stress

release:
	$(MAKE) verify
	@echo ================================================================
	@echo  Release v$(VERSION) - all gates passed. Next step: make tag

tag:
	@$(PYTHON) -c "import subprocess,sys;r=subprocess.run(['git','status','--porcelain'],capture_output=True,text=True);sys.exit('worktree not clean - commit first') if r.stdout.strip() else None"
	git tag -a v$(VERSION) -m "release v$(VERSION)"
	@echo tag v$(VERSION) created. push it with: git push origin v$(VERSION)

clean:
	@$(PYTHON) -c "import pathlib,shutil;shutil.rmtree('dist',ignore_errors=True);[shutil.rmtree(p,ignore_errors=True) for p in list(pathlib.Path('app').rglob('__pycache__'))+list(pathlib.Path('scripts').rglob('__pycache__'))];print('cleaned: dist and __pycache__')"
