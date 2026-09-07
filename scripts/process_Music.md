gather FLAC files

strip metadata from FLAC files:
```
Get-ChildItem *.flac | ForEach-Object { ffmpeg -i $_.FullName -map_metadata -1 -c:a copy "clean_$($_.Name)" }
```

if an excessive intro needs trimming:
```
Get-ChildItem *.flac | ForEach-Object { ffmpeg -i $_.FullName -map_metadata -1 -ss 10 -c:a copy "clean_$($_.Name)" }
```
