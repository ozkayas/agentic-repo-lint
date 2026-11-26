# Applying a cleanup

Only when the user, having read the report, asks for specific findings to be acted on. Apply exactly those.

## Before any change

- **Look at the target** as it is now; it may have changed since the audit.
- **Say the reach** once more for a global change: it affects every repo. Offer the per-repo alternative when one exists.
- **Back up** what git does not track, in `~/.agents-backup/<kind>/<timestamp>/` (create it with mode 700; settings can hold keys, so never `/tmp`).

## Skills

- A skill installed by a package manager for skills (for example one listed in `~/.agents/.skill-lock.json`) is removed with that tool, so its lock stays true.
- Otherwise remove the folder and every link to it in the agents' skill directories.
- If the skill holds the only copy of a script that something else runs, move the script out first and repoint whatever calls it.
- Afterwards clear mentions of the skill from READMEs and instruction files.

## Settings files

- Edit as data, not as text: load the JSON or TOML, change it, write it back, then validate it without printing it.
- Some paths are protected by the agent's own sandbox or permission system (its settings and skill folders). Let the write go through the normal permission prompt.
- Some blocks an agent may not edit for itself by design. Give the user the exact command to run instead of working around the block.

## Repo files

Stage the changes and offer a commit that holds only them. Unrelated work in the tree stays out.

## Afterwards

Re-check the findings you acted on and report what changed and what did not.
