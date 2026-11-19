from argparse import ArgumentParser
from getpass import getpass

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

from .db import engine
from .identity import create_operator


def main() -> None:
    parser = ArgumentParser(description="AIX local administration")
    commands = parser.add_subparsers(dest="command", required=True)
    operator = commands.add_parser("operator", help="Create a platform operator")
    operator.add_argument("email")
    args = parser.parse_args()
    password = getpass("Password: ")
    if password != getpass("Repeat password: "):
        parser.exit(2, "Passwords differ\n")
    with Session(engine()) as db:
        try:
            create_operator(db, args.email, password)
            db.commit()
        except ValidationError:
            parser.exit(2, "Invalid email or password\n")
        except (HTTPException, ValueError) as error:
            parser.exit(2, f"{getattr(error, 'detail', error)}\n")
    print("Operator created")


if __name__ == "__main__":
    main()
