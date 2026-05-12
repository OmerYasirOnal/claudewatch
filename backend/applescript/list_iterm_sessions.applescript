on run
    tell application "iTerm"
        set out to ""
        repeat with w in windows
            try
                set wid to id of w
            on error
                set wid to 0
            end try
            try
                set tabList to tabs of w
                set tCount to count of tabList
                repeat with j from 1 to tCount
                    set t to item j of tabList
                    try
                        set sessList to sessions of t
                        repeat with s in sessList
                            try
                                set stty to tty of s
                            on error
                                set stty to "?"
                            end try
                            try
                                set sid to unique id of s
                            on error
                                set sid to ""
                            end try
                            try
                                set sname to name of s
                            on error
                                set sname to ""
                            end try
                            set out to out & wid & "|" & j & "|" & stty & "|" & sid & "|" & sname & linefeed
                        end repeat
                    end try
                end repeat
            end try
        end repeat
        return out
    end tell
end run
