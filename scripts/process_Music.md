gather FLAC files

strip metadata from FLAC files:
```
Get-ChildItem *.flac | ForEach-Object { ffmpeg -i $_.FullName -map_metadata -1 -c:a copy "clean_$($_.Name)" }
```

xld > open folder as disc > get metadata > transcode


if an excessive intro needs trimming:
```
ffmpeg -i input.m4a -map 0:a -ss 10 -c copy -map_metadata 0 trimmed_audio_only.m4a
ffmpeg -i trimmed_audio_only.m4a -i input.m4a -map 0:a -map 1:v -c copy -disposition:v:0 attached_pic output_final.m4a
```

music > file > import > m4a files
