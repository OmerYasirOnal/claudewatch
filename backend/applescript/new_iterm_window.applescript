on run argv
    set targetCwd to item 1 of argv
    set claudeCmd to item 2 of argv
    set fullCmd to "cd " & quoted form of targetCwd & " && " & claudeCmd
    tell application "iTerm"
        activate
        set newWindow to (create window with default profile)
        tell current session of newWindow
            write text fullCmd
        end tell
    end tell
end run
