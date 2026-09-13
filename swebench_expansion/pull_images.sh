#!/bin/bash
set -uo pipefail
images=(
  swebench/sweb.eval.x86_64.astropy_1776_astropy-13033:latest
  swebench/sweb.eval.x86_64.astropy_1776_astropy-13977:latest
  swebench/sweb.eval.x86_64.astropy_1776_astropy-14369:latest
  swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-20676:latest
  swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-22865:latest
  swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-24870:latest
  swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-13142:latest
  swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102:latest
  swebench/sweb.eval.x86_64.sympy_1776_sympy-13091:latest
  swebench/sweb.eval.x86_64.sympy_1776_sympy-16597:latest
  swebench/sweb.eval.x86_64.sympy_1776_sympy-17630:latest
  swebench/sweb.eval.x86_64.sympy_1776_sympy-19783:latest
)
for img in "${images[@]}"; do
  echo "=== pulling $img ==="
  docker pull --platform linux/amd64 "$img" 2>&1 | tail -3
  df -h /System/Volumes/Data | tail -1
done
echo "ALL DONE"
