on run argv
    set targetCwd to item 1 of argv
    set claudeCmd to item 2 of argv
    set fullCmd to "cd " & quoted form of targetCwd & " && " & claudeCmd
    tell application "iTerm"
        activate
        if (count of windows) = 0 then
            set targetWindow to (create window with default profile)
        else
            set targetWindow to current window
            tell targetWindow to create tab with default profile
        end if
        tell current session of current tab of targetWindow
            write text fullCmd
        end tell
    end tell
end run
