"""Local Linux hardware Q&A assistant.

Answers natural-language questions about the host machine's hardware using
allow-listed, read-only system inspection tools. The LLM (optional) never
guesses specs — it only selects pre-defined tools whose output is parsed
into structured JSON before answer synthesis.
"""

__version__ = "0.1.0"
