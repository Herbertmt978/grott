# Datalogger TCP keepalive

Pending release note for PR #13. This change is not part of the frozen 0.1.12
release metadata; include it when preparing the next release.

Enable TCP keepalive on datalogger connections accepted by `grottserver`.
This allows the kernel to detect abandoned sessions without a FIN and helps
stateful firewalls retain quiet connections. Where supported, probes start
after 120 seconds of inactivity, repeat every 30 seconds, and use three probes.
An unsupported socket option is logged without rejecting the connection.
