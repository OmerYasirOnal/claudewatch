on run argv
    set targetTty to item 1 of argv
    tell application "iTerm"
        activate
        repeat with w in windows
            repeat with t in tabs of w
                repeat with s in sessions of t
                    try
                        if (tty of s) is targetTty then
                            select w
                            tell t to select
                            tell s to select
                            return "ok"
                        end if
                    end try
                end repeat
            end repeat
        end repeat
    end tell
    return "not_found"
end run
