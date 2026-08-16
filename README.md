<p align="center">
  <img src="assets/collector.png" width="100" height="100" alt="The Collector logo">
</p>

# The Collector


The Collector is a command line tool. It downloads League of Legends replay files.

A replay file has the .rofl extension. A replay is a record of one game. You can open a replay file in the League client and watch the game again.

The tool uses the League client to get access to Riot Games. The League client must be running and you must be logged in.

The tool collects replay files. A replay file is a record of one game. The tool writes the replay files to the output directory.


## Requirements

The League client. The client must be running and you must be logged in.

You do not need a API key for use.


## Example Usage

`collector.exe gather --patch 16.14 --max 10`

`gather` will automatically download random replays

`collector.exe get EUW1_7929523534`

## Install

You can run the tool in two ways.

### The compiled tool

Download the compiled tool from the [Releases page](https://github.com/LeonKraim/Replay-Downloader/releases) on GitHub. The file has the name collector.exe. Put the file anywhere. Run the file in a command prompt.

### From source

Do these steps:

1. Open a command prompt.
2. Go to the directory that contains the tool:
   `cd path\to\Replay-Downloader`
3. Install the Python packages:
   `python -m pip install -r requirements.txt`
4. Install the Chromium browser:
   `python -m playwright install chromium`
5. Run the tool:
   `python replay-downloader.py version`

You can also install the tool with `python -m pip install .`. Then type `replay-downloader`.

This document shows both forms. The form `collector.exe` is the compiled tool. The form `python replay-downloader.py` is the source form.

## Commands

The table shows all commands.

| Command | What the command does |
|---|---|
| gather | Walks through players and downloads random replays automatically without needing any directions. |
| get | Downloads one specific game. |
| status | Shows the progress of a gather command. |
| resolve | Finds the PUUID of a player. |
| version | Shows the version of the tool. |

The main command is gather.

## How to run a command

You run a command in a command prompt. You type the command and press Enter.

Use this form:

`collector.exe COMMAND OPTIONS`

Or the source form:

`python replay-downloader.py COMMAND OPTIONS`

The examples in this document use the compiled form. Replace `collector.exe` with `python replay-downloader.py` to use the source form.

This example shows the version command:

`collector.exe version`

The tool shows:

```
replay-downloader v0.2.0
```

## The gather command

The gather command runs the gather process. The gather command is the main command.

### What a gather does

A gather starts with one seed player. The gather produces a folder of replay files.

This example shows a complete gather:

1. You type this command:
   `collector.exe gather "Alice" --patch 16.14 --max 10`
2. The tool finds the player Alice.
3. The tool reads the match history of Alice. The match history has 200 games.
4. The tool applies the rule --patch 16.14. The tool keeps 12 games. The tool removes the other 188 games.
5. The tool downloads the replay of each of the 12 games.
6. The tool checks each download. The tool keeps a download only if it is a valid replay file. In this example, 10 downloads are valid. The tool removes the other 2 downloads.
7. The tool has 10 valid replays. The tool reached the value of --max. The tool stops.
8. The tool writes the 10 replay files to the downloads directory.

### The spiral

The gather does not stop after the first player. The gather finds more players and more replays.

The spiral works in this way:

1. Each game has up to 10 players. The 12 games of Alice contain up to 120 players.
2. The tool adds those players to the queue.
3. The tool reads the match history of each player in the queue.
4. The tool finds more games and more players.
5. The process repeats.

This is the spiral. The tool starts with one player. The tool walks outward through the players who played together. The tool finds more replays with each step.

The gather stops when one of these conditions is true:

- The tool reached the value of --max.
- The tool reached the value of --max-players.
- The tool found no more new players.
- The tool found no more new games.

### Real example

This example shows a real gather. The tool gathered the replays of the current summoner. The command limited the gather to patch 16.14 and to 2 replays:

`collector.exe gather --patch 16.14 --max 2`

The tool showed:

```
no player given: gathering the current summoner
=== gather start: seeds=1 queued=1 patches=['16.14'] map=any max=2 workers=1 min_gap=1.5s ===
status: downloaded=0 pending=0 candidates=0 excluded=0
  player 74fb2c08-8e2 games=176 new_candidates=176 queued=1413
  player 0f71af0d-39f games=950 new_candidates=1125 queued=9099
  player f11964c2-875 games=1000 new_candidates=2124 queued=16743
  player 794ba565-880 games=996 new_candidates=3119 queued=24832
  player 0ab52283-493 games=447 new_candidates=3564 queued=28166
status: downloaded=0 pending=307 candidates=307 excluded=0
  OK  EUW1_7928569279 patch=16.14 len=946.4s 5944891 bytes [ok=1]
  OK  EUW1_7928547883 patch=16.14 len=1102.2s 7495778 bytes [ok=2]
status: downloaded=2 pending=305 candidates=307 excluded=0
=== reached max 2 ===
=== gather done: downloaded=2 excluded=0 pending=305 ===
```

This is what the output means:

| Output | Meaning |
|---|---|
| player 74fb2c08-8e2 | The tool walked the history of this player. The player ID is shortened. |
| games=176 | The tool found 176 games in the history of that player. |
| new_candidates=176 | The tool added 176 new games to the candidates. |
| queued=1413 | The tool added 1413 players to the queue. |
| OK EUW1_7928569279 | The tool downloaded a valid replay. The line shows the patch, the length, and the size. |
| [ok=1] | The number of valid replays so far. |
| reached max 2 | The tool downloaded 2 valid replays. The tool stopped. |

### Real example: the default patch rule

This example shows a real gather without the --patch rule. The tool used the last 3 patches:

`collector.exe gather f9cefdf4-ea86-56e0-9138-d296106b8b54 --max 1`

The tool showed:

```
no --patch given: keeping only the last 3 patches (16.16, 16.15, 16.14). Riot removes replays of older patches from the server. Use --patch to choose other patches, including older ones.
=== gather start: seeds=1 queued=1 patches=['16.14', '16.15', '16.16'] map=any max=1 workers=1 min_gap=1.5s ===
status: downloaded=0 pending=0 candidates=0 excluded=0
  player f9cefdf4-ea8 games=934 new_candidates=934 queued=6330
  player 6df61fb5-a58 games=981 new_candidates=1914 queued=13262
  player ba06c174-6c6 games=970 new_candidates=2883 queued=20464
  player ebd67393-850 games=980 new_candidates=3862 queued=24483
  player 001b6b32-48b games=994 new_candidates=4855 queued=30944
status: downloaded=0 pending=1024 candidates=1024 excluded=0
  OK  EUW1_7952256561 patch=16.16 len=1546.7s 12950889 bytes [ok=1]
status: downloaded=1 pending=1023 candidates=1024 excluded=0
=== reached max 1 ===
=== gather done: downloaded=1 excluded=0 pending=1023 ===
```

The tool read the current patch from the League client. The tool used the last 3 patches. The tool downloaded a valid replay from patch 16.16. The tool did not need a new version for the current patch.

### Gather from the current summoner

Do this to gather the replays of the account that is logged in:

1. Type this command:
   `collector.exe gather`
2. Press Enter.

The tool starts with the current summoner. The tool downloads the replays of the last 3 patches.

### Gather from a player name

Do this to gather the replays of a specific player:

1. Type this command:
   `collector.exe gather "PLAYER_NAME"`
2. Replace PLAYER_NAME with the name of the player.
3. Press Enter.

The tool finds a player by name only if that player is your friend. For all other players, give the PUUID. Read the section "Player references".

### Gather from one patch

Do this to gather the replays of one patch only:

1. Type this command:
   `collector.exe gather "PLAYER_NAME" --patch 16.14`
2. Replace PLAYER_NAME with the name of the player.
3. Press Enter.

The tool keeps the games from patch 16.14. The tool removes all other games.

### Limit the number of replays

Do this to stop after a set number of replays:

1. Type this command:
   `collector.exe gather "PLAYER_NAME" --patch 16.14 --max 500`
2. Replace PLAYER_NAME with the name of the player.
3. Press Enter.

The tool stops when it has downloaded 500 valid replays.

Do this to walk through a set number of players only:

`collector.exe gather "PLAYER_NAME" --max-players 100`

### Gather without download

Do this to find games without downloading the replays:

`collector.exe gather "PLAYER_NAME" --no-download`

The tool writes the games to the candidates file. The tool does not download the replays.

## The get command

The get command downloads one specific game.

Do this:

1. Type this command:
   `collector.exe get EUW1_7929523534`
2. Replace EUW1_7929523534 with the game ID.
3. Press Enter.

The tool downloads the replay to the downloads directory.

You can also use a hyphen in the game ID:

`collector.exe get EUW1-7929523534`

### Real example

The tool showed:

```
OK  C:\Users\...\downloads\EUW1-7929523534.rofl  patch=16.14 len=93.0s 477687 bytes
```

The tool writes the replay to the file EUW1-7929523534.rofl. The output dir line shows where the tool put the file. The path `C:\Users\...` is the current working directory.

The get command can show other results:

| Result | Meaning |
|---|---|
| OK | The tool downloaded a valid replay. |
| already downloaded | The tool found the replay already in the downloads directory. |
| unavailable | Riot Games returned no replay for this game. The tool removes the file. |
| excluded | The tool removed the replay. A rule rejected it. The line shows the reason. |

## The status command

The status command shows the progress of a gather command.

Do this:

`collector.exe status`

The tool shows:

```
output dir : C:\Users\...\rd_demo
replays    : 0
files on disk: 0
pending    : 0
candidates : 0
excluded   : 0
```

The output dir line shows the current working directory.

This example shows the status after the real gather in this document:

```
output dir : C:\Users\...\rd_gather
replays    : 2
files on disk: 2
pending    : 3562
candidates : 3564
excluded   : 0
patches    : 14.20, 14.21, 14.23, 14.24, 15.10, ...
```

This is what the output means:

| Output | Meaning |
|---|---|
| replays | The number of valid replays that pass the rules. |
| files on disk | The number of .rofl files in the downloads directory. |
| pending | The number of discovered games that wait for download. |
| candidates | The total number of discovered games. |
| excluded | The number of rejected games. |
| patches | The patches of the discovered games. |

## The resolve command

The resolve command finds the PUUID of a player.

A PUUID is the permanent ID of a player account.

Do this:

`collector.exe resolve "PLAYER_NAME"`

The tool shows the PUUID.

### Real example

The tool resolved a friend by name. The tool showed:

```
00a5fd10-8fc0-524a-aa03-730baba25a88
```

The tool resolves a PUUID directly. The tool showed the same PUUID:

```
74fb2c08-8e2d-5556-8838-c1d94f808abc
```

The tool cannot resolve a name that is not a friend. The tool showed:

```
error: cannot resolve 'NotARealPlayerXyz_9999'. The tool only searches the friend list of the current summoner. Give a friend, or pass a PUUID.
```

## Rules

You can set rules to select the games.

The table shows all rules.

| Option | What the rule does | Default |
|---|---|---|
| --patch | Keeps only the games from this patch. Use the option several times for several patches. | The last 3 patches |
| --map | Keeps only the games from this map. Use 11 for Summoner's Rift. Use 12 for Howling Abyss. | No rule |
| --min-length | Removes the games that are shorter than this number of seconds. Use 300 to remove remakes. | No rule |
| --max | Stops the gather after this number of valid replays. | No limit |
| --max-players | Walks through at most this number of players. | No limit |
| --workers | Uses this number of download workers. The maximum is 4. | 4 |
| --min-gap | Waits at least this number of seconds between requests. | 1.5 |
| --out | Uses this directory for the output. | The current directory |

The --patch rule has a default value. Riot Games removes the replay of an old game from the server after a few patches. The game stays in the match history. The replay is gone. The tool downloads only the replays that Riot Games still stores.

By default the tool keeps the games of the last 3 patches. The current patch is 16.16. The tool reads the current patch from the League client. The tool adjusts automatically when a new patch starts. You do not need a new version of the tool.

Use the --patch rule to keep other patches, including older patches. The tool keeps exactly the patches that you give:

`collector.exe gather "PLAYER_NAME" --patch 16.16 --patch 16.15 --patch 16.14 --patch 15.24`

If the replay of an old game is gone, the download fails. Read the section Troubleshooting.

For all other rules, there is no rule by default. The tool keeps every other game that it finds.

The tool always removes a download that is not a valid replay file. This is not a rule. This is a check. The tool cannot use a download that is not a valid replay file.

## Player references

The gather command and the resolve command need a player.

You can give the player in three forms:

1. A PUUID. The tool uses the PUUID directly.
   Example: `74fb2c08-8e2d-5556-8838-c1d94f808abc`
2. A Riot ID. Use the form Name#TAG.
   Example: `HideOnBush#KR1`
3. A summoner name.
   Example: `Faker`

The tool needs no key. The tool uses the running client only.

The tool finds the player in this order:

1. If you give a PUUID, the tool uses it directly.
2. The tool looks in the friend list of the current summoner.
3. The tool searches the platform of the client.

The tool finds a player by name only if that player is your friend. For all other players, give the PUUID of the player.

## The output directory

The tool writes all data to the output directory. The default output directory is the current working directory. The current working directory is the folder where you run the command.

Use --out to use a different directory:

`collector.exe gather --out D:\replays`

The table shows the content of the output directory.

| File or directory | Content |
|---|---|
| downloads | The valid replay files. |
| candidates.tsv | The discovered games. |
| excluded.tsv | The games that the tool rejected. |
| visited.txt | The players that the tool walked through. |
| frontier.txt | The players that the tool will walk through. |
| gather.log | The event log of the gather command. |

The tool stores progress in the output directory. You can stop a gather at any time. You can run the same command again. The tool continues from where it stopped.

## Safety

The tool sends requests to Riot Games.

The tool limits the request speed on purpose. The limit protects the account.

Do not change the values of --workers and --min-gap. A high request speed can cause problems for the account.

## How the tool works

This section gives a short summary.

1. The tool gets the RSO token from the League client.
2. The tool reads the match history of the first player.
3. The tool applies the rules to each game.
4. The tool downloads the replay of each game that passes the rules.
5. The tool checks each replay from its own bytes.
6. The tool keeps the replay only if it is a valid replay file.
7. The tool adds the other players of the game to the queue.
8. The tool continues with the next player.

## Troubleshooting

| Problem | Cause | Solution |
|---|---|---|
| The tool shows an error about the client. | The League client is not running. | Start the League client and log in. Run the command again. |
| The tool shows an error about the browser. | The Chromium browser is not installed. | Run `python -m playwright install chromium`. |
| The tool cannot find a player. | The tool cannot resolve the name. | The player is not a friend of the current summoner. Give the PUUID. |
| The tool says that a game has no replay. | Riot Games removed the replay. | Try another game, or use --patch with a recent patch. |
| The tool rejected a download. | The download is not a valid replay file. | This is normal. The tool records the download in excluded.tsv. |
