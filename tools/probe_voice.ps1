param(
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$TextBase64,
    [string]$VoiceName = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$result = [ordered]@{
    status = "unverified"
    available_voices = @()
    selected_voice = $null
    output_path = $OutputPath
    error = $null
}
$synth = $null

try {
    Add-Type -AssemblyName System.Speech
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $installed = @($synth.GetInstalledVoices())
    $result.available_voices = @($installed | ForEach-Object {
        [ordered]@{
            name = $_.VoiceInfo.Name
            culture = $_.VoiceInfo.Culture.Name
            enabled = [bool]$_.Enabled
            gender = $_.VoiceInfo.Gender.ToString()
        }
    })
    $chinese = @($installed | Where-Object {
        $_.Enabled -and $_.VoiceInfo.Culture.Name.StartsWith("zh", [StringComparison]::OrdinalIgnoreCase)
    })
    if ($VoiceName) {
        $chinese = @($chinese | Where-Object { $_.VoiceInfo.Name -eq $VoiceName })
    }
    if ($chinese.Count -eq 0) {
        $result.status = "unavailable"
        $result.error = "No enabled installed Chinese System.Speech voice."
    }
    else {
        $preferred = @($chinese | Where-Object { $_.VoiceInfo.Culture.Name -eq "zh-CN" })
        $chosen = if ($preferred.Count -gt 0) { $preferred[0] } else { $chinese[0] }
        $result.selected_voice = [ordered]@{
            name = $chosen.VoiceInfo.Name
            culture = $chosen.VoiceInfo.Culture.Name
        }
        $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TextBase64))
        $synth.SelectVoice($chosen.VoiceInfo.Name)
        $format = [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(
            32000,
            [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
            [System.Speech.AudioFormat.AudioChannel]::Mono
        )
        $synth.SetOutputToWaveFile($OutputPath, $format)
        $synth.Speak($text)
        $synth.SetOutputToNull()
        $result.status = "generated"
    }
}
catch {
    $result.status = "failed"
    $result.error = $_.Exception.Message
}
finally {
    if ($null -ne $synth) { $synth.Dispose() }
}
[Console]::WriteLine(($result | ConvertTo-Json -Depth 6))
if ($result.status -eq "generated") { exit 0 }
exit 2

