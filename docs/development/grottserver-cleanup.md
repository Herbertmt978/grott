# Datalogger connection cleanup

Pending release note for PR #14. Keep the frozen 0.1.12 release metadata
unchanged; include this note when preparing the next release.

Remember the peer address returned by `accept()` instead of querying a dead
socket during cleanup or queued writes. Remove the connection's own send queue
and matching logger registration, tolerate repeated cleanup, and close the
socket even when registry cleanup fails.

When a reset connection reconnects from the same address and port, retire the
old socket before registering the replacement queue. Late select events for
the old socket must not remove the new queue or logger registration.
