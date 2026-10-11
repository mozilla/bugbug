# Global

Do not overengineer for the use cases you were not asked to handle.

# Docs

Double check if the docs should be updated in the `/docs` folder.
Make sure the docs you write are well-structured, concise and human-readable.
Link to the code where appropriate instead of repeating implementation details in the docs.
Follow the existing style.

# Comments

Limit the amount of comments you put in the code to a strict minimum.
You should almost never add comments, except sometimes on non-trivial code, function definitions if the arguments aren't self-explanatory, and class definitions and their members.
Aim at the code being self-documented.
Do not remove existing comments unless they are directly related to what you are changing.
If you do write comments, be concise.
Do not add a comment explanation to every thing you were asked to correct.

# Code Style

Our style guide forbids the use of emoji.
Make sure the code is simple and concise.

# Specs

Be concise in writing specs so that they are easily human-readable in short amount of time and easily comprehensible.
Do not repeat yourself.

# Security

NEVER read .env files. They might include secrets.
