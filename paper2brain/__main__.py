import os

if os.environ.get("P2B_SERVER"):
    from .remote import main
else:
    from .cli import main

main()
