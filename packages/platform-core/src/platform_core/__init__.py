"""What every service of the platform shares, written once.

Each module is small and has one job. The services import what they use; the
optional dependencies (OpenTelemetry, a2a-sdk, Agent Framework, psycopg) are
imported lazily, so a service never carries a library it has no use for.
"""
