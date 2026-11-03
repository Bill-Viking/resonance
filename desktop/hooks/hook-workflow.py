"""Resonance's local workflow.py is not the unrelated PyPI workflow package.

Override the contributed metadata hook for that package. Our module uses only
ordinary imports, which PyInstaller analyzes directly; it has no package metadata
or hidden data files to collect.
"""
datas = []
