"""Extraction (spec 008): adapters, selection, quality scoring, the extraction file writer.

The CLI commands ``extract``, ``extract-record`` and ``extractors`` are thin wrappers over
``pipeline`` so that other specs (placement, intake staging, lint) can call the same functions.
"""
