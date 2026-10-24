"""ldraw-annotator-nova: fast, resumable annotation of LDraw models.

Pipeline (see AGENTS.md): prep (mpd + flex + part comments) -> rounds of single-shot LLM
decisions (digest -> plan) applied by plan.py -> refine -> write -> check.
"""
