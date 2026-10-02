"""Parse an Apple Health export into aggregates and one insight.

The ingest command does not talk to Google Drive and does not return raw
Health XML. The optional MCP server can list and download a Drive zip when a
token is set, and its tools return aggregates only.
"""

__version__ = "0.1.0"
