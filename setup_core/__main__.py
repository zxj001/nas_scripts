import sys

if len(sys.argv) > 1 and sys.argv[1] == "--worker":
    from setup_core.worker import main

    sys.argv.pop(1)
else:
    from setup_core.cli import main
raise SystemExit(main())
