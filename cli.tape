# VHS documentation
#
# Output:
#   Output <path>.gif               Create a GIF output at the given <path>
#   Output <path>.mp4               Create an MP4 output at the given <path>
#   Output <path>.webm              Create a WebM output at the given <path>
#
# Require:
#   Require <string>                Ensure a program is on the $PATH to proceed
#
# Settings:
#   Set FontSize <number>           Set the font size of the terminal
#   Set FontFamily <string>         Set the font family of the terminal
#   Set Height <number>             Set the height of the terminal
#   Set Width <number>              Set the width of the terminal
#   Set LetterSpacing <float>       Set the font letter spacing (tracking)
#   Set LineHeight <float>          Set the font line height
#   Set LoopOffset <float>%         Set the starting frame offset for the GIF loop
#   Set Theme <json|string>         Set the theme of the terminal
#   Set Padding <number>            Set the padding of the terminal
#   Set Framerate <number>          Set the framerate of the recording
#   Set PlaybackSpeed <float>       Set the playback speed of the recording
#   Set MarginFill <file|#000000>   Set the file or color the margin will be filled with.
#   Set Margin <number>             Set the size of the margin. Has no effect if MarginFill isn't set.
#   Set BorderRadius <number>       Set terminal border radius, in pixels.
#   Set WindowBar <string>          Set window bar type. (one of: Rings, RingsRight, Colorful, ColorfulRight)
#   Set WindowBarSize <number>      Set window bar size, in pixels. Default is 40.
#   Set TypingSpeed <time>          Set the typing speed of the terminal. Default is 50ms.
#
# Sleep:
#   Sleep <time>                    Sleep for a set amount of <time> in seconds
#
# Type:
#   Type[@<time>] "<characters>"    Type <characters> into the terminal with a
#                                   <time> delay between each character
#
# Keys:
#   Escape[@<time>] [number]        Press the Escape key
#   Backspace[@<time>] [number]     Press the Backspace key
#   Delete[@<time>] [number]        Press the Delete key
#   Insert[@<time>] [number]        Press the Insert key
#   Down[@<time>] [number]          Press the Down key
#   Enter[@<time>] [number]         Press the Enter key
#   Space[@<time>] [number]         Press the Space key
#   Tab[@<time>] [number]           Press the Tab key
#   Left[@<time>] [number]          Press the Left Arrow key
#   Right[@<time>] [number]         Press the Right Arrow key
#   Up[@<time>] [number]            Press the Up Arrow key
#   Down[@<time>] [number]          Press the Down Arrow key
#   PageUp[@<time>] [number]        Press the Page Up key
#   PageDown[@<time>] [number]      Press the Page Down key
#   Ctrl+<key>                      Press the Control key + <key> (e.g. Ctrl+C)
#
# Display:
#   Hide                            Hide the subsequent commands from the output
#   Show                            Show the subsequent commands in the output
#   ScrollUp[@<time>] [number]      Scroll terminal viewport up by rows
#   ScrollDown[@<time>] [number]    Scroll terminal viewport down by rows

Output docs/assets/cli.gif

Set Shell zsh
Set FontSize 22
Set Width 1200
Set Height 500
Set BorderRadius 20

# --- Set theme ---
Set Theme {
  "name": "robbyrussell-inspired",
  "background": "#1e1e1e",
  "foreground": "#d4d4d4",
  "cursor": "#ffffff",

  "black": "#000000",
  "red": "#cd3131",
  "green": "#0dbc79",
  "yellow": "#e5e510",
  "blue": "#2472c8",
  "magenta": "#bc3fbc",
  "cyan": "#11a8cd",
  "white": "#e5e5e5",

  "brightBlack": "#666666",
  "brightRed": "#cd3131",
  "brightGreen": "#23d18b",
  "brightYellow": "#f5f543",
  "brightBlue": "#3b8eea",
  "brightMagenta": "#d670d6",
  "brightCyan": "#29b8db",
  "brightWhite": "#ffffff"
}

# --- off-camera setup: pin a reproducible prompt ---
Hide
Type `PROMPT='> '`
Enter
Ctrl+L
Show

# --- setup configuration in tmp folder ---
Hide
Type "cd $(mktemp -d)"
Enter
Type "printf 'id,feature,label\n1,0.5,cat\n2,0.9,dog\n3,0.2,cat\n' > data.csv"
Enter
Type "export LOGNAME=user USER=user"
Enter
Type "clear"
Enter
Show

# --- init store ---
Type "dstrack init"
Enter
Sleep 3s

# --- track file ---
Type "dstrack track data.csv"
Enter
Sleep 3s
Type "dstrack track data.csv"
Enter
Sleep 3s

# --- show dataset log ---
Type "dstrack log data.csv -y"
Enter
Sleep 5s

# --- Finish recording ---
Sleep 2s
