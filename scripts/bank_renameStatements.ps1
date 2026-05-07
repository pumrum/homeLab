$envPath = "secrets.env"  # adjust to your actual path
Get-Content $envPath | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*?)\s*=\s*(.*)\s*$') {
        Set-Item -Path "Env:$($matches[1])" -Value $matches[2]
    }
}
# Define the directory path where the files are located.
$PathBankStatements = $env:PATH_BANK_STATEMENTS
$BankName = $env:BANK_NAME

# Get all .pdf files in the directory.
$files = Get-ChildItem -Path $PathBankStatements -Filter "*.pdf"

# Loop through each file
foreach ($file in $files) {
    # Extract the date part and the account number part from the file name.
    if ($file.Name -match '(\d{8})-statements-(\d{4})-\.pdf') {
        $datePart = $Matches[1]
        $accountPart = $Matches[2]

        # Reformat the date.
        $formattedDate = $datePart -replace '(\d{4})(\d{2})(\d{2})', '$1-$2-$3'

        # Construct the new file name.
        $newName = "${formattedDate}_${BankName}_${accountPart}.pdf"

        # Create the full path with the new file name.
        $newFullPath = Join-Path -Path $PathBankStatements -ChildPath $newName

        # Rename the file.
        Rename-Item -Path $file.FullName -NewName $newFullPath
    }
}