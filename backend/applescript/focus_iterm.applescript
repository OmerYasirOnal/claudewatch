on run argv
    set targetWindowId to (item 1 of argv) as integer
    set targetTabId to (item 2 of argv) as integer
    tell application "iTerm"
        activate
        repeat with w in windows
            if id of w is targetWindowId then
                select w
                repeat with t in tabs of w
                    if id of t is targetTabId then
                        tell t to select
                        exit repeat
                    end if
                end repeat
                exit repeat
            end if
        end repeat
    end tell
end run
