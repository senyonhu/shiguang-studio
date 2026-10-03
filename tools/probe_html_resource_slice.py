"""Validate byte-range stream behavior in the actual compiled HTML host DLL."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / 'runtimes/html-host-dependencies/dotnet-sdk-10.0.401/dotnet.exe'
SOURCE = r'''
using System.Reflection;
using System.Text.Json;

internal static class ResourceSliceProbe {
 static int Main(string[] args) {
  var host = Assembly.LoadFrom(args[0]);
  var type = host.GetType("RenPyBridge.HtmlEmbeddedHost.ResourceSlice", true)!;
  var create = type.GetConstructor(BindingFlags.NonPublic|BindingFlags.Instance, null, new[]{typeof(string),typeof(long),typeof(long)},null)!;
  var file=Path.Combine(args[1],"range.bin");
  var bytes=Enumerable.Range(0,100).Select(x=>(byte)x).ToArray();File.WriteAllBytes(file,bytes);
  var checks=new List<string>();
  void Check(bool condition,string name){if(!condition)throw new Exception(name);checks.Add(name);}
  using(var stream=(Stream)create.Invoke(new object[]{file,17L,23L})){
   Check(stream.Length==23&&stream.Position==0,"declared length and initial position");
   using var output=new MemoryStream();var buffer=new byte[7];int count;
   while((count=stream.Read(buffer,0,buffer.Length))>0)output.Write(buffer,0,count);
   Check(output.ToArray().SequenceEqual(bytes[17..40]),"uneven chunk reads exactly declared slice");
   Check(stream.Read(buffer,0,7)==0&&stream.Position==23,"EOF prevents trailing bytes");
   Check(stream.Seek(-5,SeekOrigin.End)==18,"seek from slice end");
   var span=new byte[20];count=stream.Read(span.AsSpan());
   Check(count==5&&span[..5].SequenceEqual(bytes[35..40]),"span read clamps at slice boundary");
   stream.Position=0;Check(stream.ReadByte()==17,"position is relative to slice");
   bool lower=false,upper=false;try{stream.Position=-1;}catch(IOException){lower=true;}
   try{stream.Position=24;}catch(IOException){upper=true;}
   Check(lower&&upper,"out of bounds seeks rejected");
  }
  using(var empty=(Stream)create.Invoke(new object[]{file,0L,0L}))Check(empty.Length==0&&empty.ReadByte()==-1,"zero length slice EOF");
  File.Delete(file);Check(!File.Exists(file),"disposed response releases file handle");
  Console.WriteLine(JsonSerializer.Serialize(new{status="passed",checks}));return 0;
 }
}
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host-dll',type=Path,default=ROOT/'runtimes/html-minigame-embedded-host/win-x64/HtmlMinigameEmbeddedHost.dll')
    args=parser.parse_args()
    dll=args.host_dll.resolve(strict=True)
    run=ROOT/'evidence/html-minigames/network-boundary/resource-slice'/str(time.time_ns())
    run.mkdir(parents=True,exist_ok=False)
    (run/'ResourceSliceProbe.cs').write_text(SOURCE,encoding='utf-8')
    (run/'ResourceSliceProbe.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0-windows</TargetFramework><UseWindowsForms>true</UseWindowsForms><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>\n',encoding='utf-8')
    env=os.environ.copy()
    env.update(DOTNET_ROOT=str(SDK.parent),DOTNET_CLI_HOME=str(SDK.parent.parent/'dotnet-cli'),NUGET_PACKAGES=str(SDK.parent.parent/'nuget'),DOTNET_CLI_TELEMETRY_OPTOUT='1')
    command=[str(SDK),'run','--project',str(run/'ResourceSliceProbe.csproj'),'-c','Release','--',str(dll),str(run)]
    result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
    (run/'stdout.log').write_text(result.stdout,encoding='utf-8')
    (run/'stderr.log').write_text(result.stderr,encoding='utf-8')
    actual=json.loads(result.stdout.strip().splitlines()[-1]) if result.returncode==0 else None
    report={'status':'passed' if result.returncode==0 and actual['status']=='passed' else 'failed',
            'scope':'real compiled native DLL byte-range Stream, no browser/gameplay claim',
            'host_dll':str(dll),'host_dll_sha256':hashlib.sha256(dll.read_bytes()).hexdigest(),
            'checks':actual['checks'] if actual else [],'command':command,'exit_code':result.returncode}
    (run/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'report':str(run/'report.json'),'checks':len(report['checks'])}))
    return 0 if report['status']=='passed' else 1

if __name__=='__main__':
    raise SystemExit(main())
