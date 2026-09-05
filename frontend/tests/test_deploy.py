"""Exercise the actual deploy shell with isolated command doubles, without a build/server."""
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
DOUBLE = r'''#!/usr/bin/env python3
import io, json, os, pathlib, sys, tarfile
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
root=pathlib.Path(os.environ['TEST_ROOT'])
with (root/'calls').open('a') as f: f.write(json.dumps([name,*args])+'\n')
fail=os.environ.get('FAIL','')
if name=='id':
 print('0' if args==['-u'] else 'staff')
elif name=='flock':
 sys.exit(1 if fail=='lock' else 0)
elif name=='sudo':
 if args[:1]==['-u']: args=args[3:]
 # Do not probe Linux /home through macOS automount in these command doubles.
 args=[arg for arg in args if not arg.startswith('HOME=')]
 os.execvp(args[0],args)
elif name=='node': print('20' if '-p' in args else 'v20.20.2')
elif name=='git':
 if args[0]=='rev-parse': print('a'*40)
 elif args[0]=='archive':
  with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as t:
   for key,value in {'.nvmrc':b'20\n','package.json':b'{}'}.items():
    i=tarfile.TarInfo(key);i.size=len(value);t.addfile(i,io.BytesIO(value))
elif name=='install':
 import shutil
 mode=int(args[args.index('-m')+1],8)
 if '-d' in args:
  i=0
  while i<len(args):
   if args[i] in ['-o','-g','-m']: i+=2;continue
   if args[i]=='-d': i+=1;continue
   pathlib.Path(args[i]).mkdir(parents=True,exist_ok=True);os.chmod(args[i],mode);i+=1
 else:
  shutil.copyfile(args[-2],args[-1]);os.chmod(args[-1],mode)
elif name=='npm':
 if fail=='build' and args==['run','build']: sys.exit(1)
 if args==['run','build']:
  pathlib.Path('.next').mkdir();pathlib.Path('.next/BUILD_ID').write_text('new')
  pathlib.Path('.next/static').mkdir();pathlib.Path('.next/static/new.js').write_text('new')
elif name=='systemctl':
 if args[0]=='is-active':
  unit=args[-1]
  if unit=='nextjs.service': sys.exit(0 if os.environ.get('LEGACY','true')=='true' else 3)
  sys.exit(3 if fail=='inactive' and unit=='nextjs@3001.service' else 0)
 if fail=='start' and args[:2]==['enable','--now']: sys.exit(1)
elif name=='curl': print('500' if fail=='health' else '200',end='')
elif name=='caddy':
 if args[0]=='reload':
  marker=root/'reloaded'
  first=not marker.exists();marker.touch()
  if fail=='recovery' or (fail=='reload' and first):sys.exit(1)
 if args[0]=='validate' and fail=='validate' and '3001' in pathlib.Path(os.environ['UPSTREAM']).read_text():sys.exit(1)
'''

def command_bin(root):
    bin_dir=root/'bin';bin_dir.mkdir()
    for name in ['id','flock','sudo','node','git','install','npm','systemctl','curl','caddy','sleep']:
        p=bin_dir/name;p.write_text(DOUBLE.replace("#!/usr/bin/env python3", "#!" + sys.executable));p.chmod(0o755)
    return bin_dir

class DeployTest(unittest.TestCase):
    def run_deploy(self, fail='', legacy=True, active=3000, imports=2, hardcoded=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        root=Path(tmp.name);bin_dir=command_bin(root)
        app=root/'app';app.mkdir();(app/'.nvmrc').write_text('20')
        (app/'.env.production').write_text('TEST_ONLY=fixture')
        (app/'live-marker').write_text('old')
        (app/'.next/static').mkdir(parents=True);(app/'.next/static/old.js').write_text('old')
        slots=root/'slots';slots.mkdir();(slots/str(active)).mkdir()
        upstream=root/'upstream';old=f'reverse_proxy [::1]:{active} {{\n    header_up X-Forwarded-Host {{host}}\n}}\n';upstream.write_text(old)
        (root/'Caddyfile').write_text(f'import {upstream}\n' * imports + ('reverse_proxy [::1]:3000 {\n}\n' if hardcoded else ''))
        env={'PATH':str(bin_dir)+':/usr/bin:/bin','HOME':str(root),'TEST_ROOT':str(root),
             'APP_ROOT':str(app),'SLOTS_ROOT':str(slots),'NODE_BIN':str(bin_dir),
             'UPSTREAM':str(upstream),'CADDY_CONFIG':str(root/'Caddyfile'),
             'LOCK_FILE':str(root/'lock'),'BUILD_USER':'solo','DRAIN_SECONDS':'0','HEALTH_ATTEMPTS':'2',
             'SOURCE_PREPARED':'true','REVISION':'a'*40,'FAIL':fail,'LEGACY':str(legacy).lower()}
        result=subprocess.run(['bash',str(ROOT/'frontend/deploy.sh')],env=env,capture_output=True,text=True,timeout=120)
        calls=[json.loads(line) for line in (root/'calls').read_text().splitlines()]
        self.assertEqual('old',(app/'live-marker').read_text())
        return result,calls,upstream,old,root

    def test_success_builds_before_switch_and_stops_legacy_last(self):
        result,calls,upstream,_,root=self.run_deploy()
        self.assertEqual(0,result.returncode,result.stderr+result.stdout)
        self.assertIn('3001',upstream.read_text())
        build=calls.index(['npm','run','build'])
        ready=next(i for i,c in enumerate(calls) if c[0]=='curl')
        reload=next(i for i,c in enumerate(calls) if c[:2]==['caddy','reload'])
        stop=calls.index(['systemctl','disable','--now','nextjs.service'])
        self.assertLess(build,ready);self.assertLess(ready,reload);self.assertLess(reload,stop)
        self.assertEqual('TEST_ONLY=fixture',(root/'slots/3001/.env.production').read_text())
        self.assertEqual('old',(root/'slots/3001/.next/static/old.js').read_text())
        self.assertEqual('new',(root/'slots/3001/.next/static/new.js').read_text())
        self.assertFalse((root/'slots/3001/.next/current-static/old.js').exists())

    def test_single_frontend_domain_is_supported(self):
        result,calls,upstream,_,_=self.run_deploy(imports=1)
        self.assertEqual(0,result.returncode,result.stderr+result.stdout)
        self.assertIn('3001',upstream.read_text())
        self.assertIn(['systemctl','disable','--now','nextjs.service'],calls)

    def test_missing_or_partial_migration_does_not_start_build(self):
        for imports,hardcoded in [(0,False),(1,True)]:
            with self.subTest(imports=imports,hardcoded=hardcoded):
                result,calls,upstream,old,_=self.run_deploy(imports=imports,hardcoded=hardcoded)
                self.assertNotEqual(0,result.returncode)
                self.assertEqual(old,upstream.read_text())
                self.assertNotIn(['npm','ci'],calls)
                self.assertFalse(any(c[0]=='systemctl' for c in calls))

    def test_second_deploy_alternates_and_removes_retired_slot(self):
        result,calls,upstream,_,root=self.run_deploy(legacy=False,active=3001)
        self.assertEqual(0,result.returncode,result.stderr+result.stdout)
        self.assertIn('3000',upstream.read_text())
        self.assertFalse((root/'slots/3001').exists())
        self.assertIn(['systemctl','disable','--now','nextjs@3001.service'],calls)

    def test_failures_preserve_old_service_and_route(self):
        for failure in ['lock','build','start','health','inactive','validate','reload']:
            with self.subTest(failure=failure):
                result,calls,upstream,old,_=self.run_deploy(failure)
                self.assertNotEqual(0,result.returncode)
                self.assertEqual(old,upstream.read_text())
                self.assertNotIn(['systemctl','disable','--now','nextjs.service'],calls)
                self.assertNotIn(['systemctl','stop','nextjs.service'],calls)

    def test_ambiguous_reload_keeps_both_services_and_blocks_retry(self):
        result,calls,_,_,root=self.run_deploy('recovery')
        self.assertNotEqual(0,result.returncode)
        self.assertTrue((root/'lock.recovery').exists())
        self.assertNotIn(['systemctl','disable','--now','nextjs@3001.service'],calls)
        self.assertNotIn(['systemctl','disable','--now','nextjs.service'],calls)

class InstallTest(unittest.TestCase):
    def test_install_preserves_legacy_port_and_does_not_reload_or_start_apps(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);bin_dir=command_bin(root)
            source=root/'infra'
            for name in ['frontend','caddy','systemd']:(source/name).mkdir(parents=True)
            state=root/'state';slots=root/'slots';deployed=root/'deployed'
            legacy=root/'legacy.caddy';legacy.write_text('reverse_proxy [::1]:3001 {\n    header_up X-Forwarded-Host {host}\n}\n')
            script=(ROOT/'frontend/install-server.sh').read_text()
            # Only relocate production paths. All control flow is the real installer.
            for old,new in {
                '/var/lib/getreplay-frontend':str(state),
                '/home/solo/getreplay-front-slots':str(slots),
                '/usr/local/libexec/getreplay-release/deploy/frontend':str(deployed),
                '/etc/caddy/frontend-upstream.caddy':str(legacy),
                '/run/lock/getreplay-frontend.lock':str(root/'legacy.lock'),
                '/etc/systemd/system/nextjs@.service':str(root/'installed.service'),
            }.items():
                self.assertIn(old,script);script=script.replace(old,new)
            (source/'frontend/install-server.sh').write_text(script)
            (source/'frontend/deploy.sh').write_text('new adapter')
            (source/'systemd/nextjs@.service').write_text('template unit')
            (source/'caddy/frontend-upstream.caddy').write_text('should not replace legacy port')
            env={'PATH':str(bin_dir)+':/usr/bin:/bin','HOME':str(root),'TEST_ROOT':str(root)}
            command=['bash',str(source/'frontend/install-server.sh')]
            first=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,first.returncode,first.stderr)
            self.assertEqual(legacy.read_text(),(state/'upstream.caddy').read_text())
            self.assertTrue(slots.is_dir());self.assertEqual('new adapter',(deployed/'deploy.sh').read_text())
            # An already selected managed port wins over the legacy file on reinstall.
            (state/'upstream.caddy').write_text('existing managed configuration')
            second=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30)
            self.assertEqual(0,second.returncode,second.stderr)
            self.assertEqual('existing managed configuration',(state/'upstream.caddy').read_text())
            calls=[json.loads(line) for line in (root/'calls').read_text().splitlines()]
            self.assertTrue(all(c==['systemctl','daemon-reload'] for c in calls if c[0]=='systemctl'))
            self.assertFalse(any(c[0] in ['caddy','npm'] for c in calls))

    def test_release_installer_provisions_before_starting_broker(self):
        script=(ROOT/'release/install-server.sh').read_text()
        self.assertLess(script.index('"$INFRA_ROOT/frontend/install-server.sh"'),script.index('systemctl enable --now getreplay-release-broker.service'))

if __name__=='__main__': unittest.main()
