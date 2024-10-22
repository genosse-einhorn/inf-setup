#!/usr/bin/python3

from argparse import ArgumentParser
from configparser import ConfigParser
import os
import shutil
import pkgutil
import tempfile
import subprocess
import collections


def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def is_ascii_filename_char(c):
    return ((c >= 'a' and c <= 'z') or
            (c >= 'A' and c <= 'Z') or
            (c >= '0' and c <= '9') or
            c == '_' or c == '~' or c == '.' or c == '-')

def make_83_filename(basename, extension, number=0):
    basename = ''.join([i if is_ascii_filename_char(i) else '_' for i in basename]).upper()
    extension = ''.join([i if is_ascii_filename_char(i) else '_' for i in extension]).upper()

    if number == 0:
        return '{}{}'.format(basename[0:8], extension[0:4])
    else:
        numstr = str(number)
        return '{}~{}{}'.format(basename[0:7-len(numstr)], numstr, extension[0:4])

def load_data(package, subpackage, filename):
    if package is not None and len(package) > 0:
        p = package + '.' + subpackage
    else:
        p = subpackage

    return pkgutil.get_data(p, filename)

def quoted_str(s):
    return '"{}"'.format(s.replace('%', '%%').replace('"', '""'))

class FileTargetDir:
    _groupcounter = 1

    def __init__(self, dirid, subdir):
        self.groupno = FileTargetDir._groupcounter
        FileTargetDir._groupcounter += 1
        self.dirid = dirid
        self.subdir = subdir
        self.files = {}

    def add_file(self, targetfile, sourcefile):
        self.files[targetfile] = sourcefile

    @property
    def section_lines(self):
        for target, source in self.files.items():
            yield '{},{},,7'.format(quoted_str(target), quoted_str(source))

    @property
    def source_files(self):
        for target, source in self.files.items():
            yield source

    @property
    def destination_dir(self):
        return '{},{}'.format(self.dirid, quoted_str(self.subdir))

    def as_del_dirs_line(self):
        return '"%{}%\\{}'.format(self.dirid, quoted_str(self.subdir)[1:])

    @property
    def section_title(self):
        return 'CopyFiles{}'.format(self.groupno)

    def has_files(self):
        return len(self.files)

class SourceFileCollector:
    def __init__(self, outdir):
        self.out_files = []
        self.res_names = []
        self.outdir = outdir
        self.totalsize = 0
        self.disk_associations = {}

    def synth_file(self, fname):
        self.out_files.append(fname)

    def reserve_name(self, fname):
        self.res_names.append(fname)

    def copy_file(self, origfile):
        origdir, origfname = os.path.split(origfile)
        origbasename, origextension = os.path.splitext(origfname)

        i = 0
        dosname = make_83_filename(origbasename, origextension, i)
        while dosname in self.out_files or dosname in self.res_names:
            i += 1
            dosname = make_83_filename(origbasename, origextension, i)

        shutil.copyfile(origfile, os.path.join(self.outdir, dosname))
        self.out_files.append(dosname)

        self.totalsize += os.path.getsize(origfile)

        return dosname

    def set_file_disk(self, filename, diskno):
        self.disk_associations[filename] = diskno

    @property
    def source_disk_lines(self):
        for f in self.out_files:
            yield '{}={}'.format(f, self.disk_associations.get(f, '1'))

class InfLikeFileBuilder:
    def __init__(self):
        self.clear()

    def clear(self):
        self._data = collections.OrderedDict() # dict[str, list[str]]

    def add_whole_section(self, section, lines):
        self._data[section] = list(lines)

    def add_line(self, section, line):
        if not section in self._data:
            self._data[section] = []

        self._data[section].append(line)

    def set_value(self, section, key, value):
        if not section in self._data:
            self._data[section] = []

        for i in range(0, len(self._data[section])):
            if self._data[section][i].startswith(key + '='):
                self._data[section][i] = '{}={}'.format(key, value)
                break
        else:
            self._data[section].append('{}={}'.format(key, value))

    def append_to_list_value(self, section, key, item):
        if not section in self._data:
            self._data[section] = []

        for i in range(0, len(self._data[section])):
            if self._data[section][i].startswith(key + '='):
                oldval = self._data[section][i][len(key)+1:]
                self._data[section][i] = '{}={},{}'.format(key, oldval, item)
                break
        else:
            self._data[section].append('{}={}'.format(key, item))

    def section_lines(self, section):
        if not section in self._data:
            return []

        return list(self._data[section])

    def value(self, section, key):
        if not section in self._data:
            return None

        for i in range(0, len(self._data[section])):
            if self._data[section][i].startswith(key + '='):
                return self._data[section][i][len(key)+1:]

        return None

    def to_str(self):
        lines = []

        for section, content in self._data.items():
            lines.append('[{}]'.format(section))
            for c in content:
                lines.append(c)
            lines.append('')

        return '\r\n'.join(lines)

    def write_to_file(self, filepath):
        s = self.to_str()

        if is_ascii(s):
            encoding = 'ASCII'
        else:
            encoding = 'utf-16'

        with open(filepath, 'w', encoding=encoding, newline='') as f:
            f.write(s)

class InfFileBuilder:
    def __init__(self, outdir, infname):
        self.outdir = outdir
        self.infname = infname
        self.cabfiles = SourceFileCollector(outdir)
        self.disks = {'1': 'Installation Files'}
        self.copysecs = []
        self.uninstall_id = None
        self.title = None
        self.publisher = None
        self.shortcut = None
        self.copy_bootstrapper = False
        self.installbeginprompt = None
        self.installendprompt = None
        self.advanced_inf = False

        self.cabfiles.synth_file(infname + '.INF')
        self.cabfiles.reserve_name(infname + '.EXE') # for potential bootstrapper
        self.cabfiles.reserve_name('ADVPACK.DLL')  # IEXPRESS might add its own file here
        self.cabfiles.reserve_name('W95INF16.DLL') # ^
        self.cabfiles.reserve_name('W95INF32.DLL') # ^

    def _process_source_files_recourse(self, dirid, subdir_list, sourcedir):
        t = FileTargetDir(dirid, '\\'.join(subdir_list))

        for i in os.listdir(sourcedir):
            path = os.path.join(sourcedir, i)
            if os.path.isfile(path):
                cabname = self.cabfiles.copy_file(path)
                t.add_file(i, cabname)

            if os.path.isdir(path):
                for k in self._process_source_files_recourse(dirid, subdir_list + [i], path):
                    yield k

        if t.has_files():
            yield t

    def _process_source_files(self, source_dir):
        for i in os.listdir(source_dir):
            path = os.path.join(source_dir, i)
            if not os.path.isdir(path):
                raise Exception('‘{}’ is not a directory'.format(path))

            dirid = int(i)
            subdir_list = []

            for k in self._process_source_files_recourse(dirid, subdir_list, path):
                yield k

    def add_source_files(self, sourcedir):
        for k in self._process_source_files(sourcedir):
            self.copysecs.append(k)

    def write_inf_file(self):
        inf = InfLikeFileBuilder()

        inf.set_value('Version', 'Signature', '$CHICAGO$')

        if self.advanced_inf:
            inf.set_value('Version', 'AdvancedINF', '2.5')

        if self.publisher is not None:
            inf.set_value('Version', 'Provider', quoted_str(self.publisher))

        # install
        for s in self.copysecs:
            inf.append_to_list_value('DefaultInstall', 'CopyFiles', s.section_title)
            inf.add_whole_section(s.section_title, s.section_lines)
            inf.set_value('DestinationDirs', s.section_title, s.destination_dir)


        # prompts
        if self.advanced_inf:
            inf.set_value('DefaultInstall', 'RequiredEngine', 'SetupAPI,"Fatal Error - need setupapi.dll"')
            inf.set_value('DefaultInstall', 'UpdateAdvDlls', '1')
            inf.set_value('DefaultInstall', 'BeginPrompt', 'InstallBeginPrompt')
            inf.set_value('InstallBeginPrompt', 'Title', quoted_str(self.title or self.infname))

            if self.installbeginprompt is not None:
                inf.set_value('InstallBeginPrompt', 'Prompt', quoted_str(self.installbeginprompt))
                inf.set_value('InstallBeginPrompt', 'ButtonType', 'YESNO')

            if self.installendprompt is not None:
                inf.set_value('DefaultInstall', 'EndPrompt', 'InstallEndPrompt')
                inf.set_value('InstallEndPrompt', 'Prompt', quoted_str(self.installendprompt))

        # uninstall
        if self.uninstall_id is not None:
            inf.append_to_list_value('DefaultInstall', 'CopyFiles', 'UninstallCopyInfFile')

            inf.append_to_list_value('DefaultInstall', 'AddReg', 'UninstallRegKeys')

            for s in self.copysecs:
                inf.append_to_list_value('DefaultUninstall', 'DelFiles', s.section_title)

            inf.append_to_list_value('DefaultUninstall', 'DelReg', 'UninstallRegKeyDel')

            if self.advanced_inf:
                inf.set_value('DefaultUninstall', 'RequiredEngine', 'SetupAPI,"Fatal Error - need setupapi.dll"')
                inf.append_to_list_value('DefaultUninstall', 'DelDirs', 'UninstallDelDirs')
                for s in self.copysecs:
                    inf.add_line('UninstallDelDirs', s.as_del_dirs_line())

                inf.append_to_list_value('DefaultUninstall', 'BeginPrompt', 'UninstallBeginPrompt')
                inf.append_to_list_value('DefaultUninstall', 'EndPrompt', 'UninstallEndPrompt')
                inf.set_value('UninstallBeginPrompt', 'Title', quoted_str(self.title or self.infname))
                inf.set_value('UninstallBeginPrompt', 'Prompt', quoted_str('Do you really want to uninstall {}?'.format(self.title or self.infname)))
                inf.set_value('UninstallBeginPrompt', 'ButtonType', 'YESNO')
                inf.set_value('UninstallEndPrompt', 'Prompt', quoted_str('{} has been uninstalled successfully.'.format(self.title or self.infname)))


            inf.append_to_list_value('DefaultUninstall', 'DelFiles', 'UninstallCopyInfFile')
            inf.add_line('UninstallCopyInfFile', '{}.INF,{}.INF,,7'.format(self.uninstall_id, self.infname))
            inf.set_value('DestinationDirs', 'UninstallCopyInfFile', '10,INF')


            inf.append_to_list_value('DefaultUninstall', 'DelFiles', 'UninstallDeletePnfFile')
            inf.add_line('UninstallDeletePnfFile', '{}.PNF,,,7'.format(self.uninstall_id))
            inf.set_value('DestinationDirs', 'UninstallDeletePnfFile', '10,INF')

            inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","DisplayName",{},"{}"'.format(self.uninstall_id, 0x4000, self.title or self.uninstall_id))

            if self.advanced_inf:
                inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","UninstallString",{},"rundll32.exe advpack.dll,LaunchINFSectionEx {}.INF,DefaultUninstall,,0,"'.format(self.uninstall_id, 0x4000, self.uninstall_id))
            else:
                inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","UninstallString",{},"rundll32.exe setupapi.dll,InstallHinfSection DefaultUninstall 132 %10%\INF\{}.INF"'.format(self.uninstall_id, 0x4000, self.uninstall_id))

            inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","NoModify",{},1'.format(self.uninstall_id, 0x10001 | 0x4000))
            inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","NoRepair",{},1'.format(self.uninstall_id, 0x10001 | 0x4000))
            inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","EstimatedSize",{},{}'.format(self.uninstall_id, 0x10001 | 0x4000, self.cabfiles.totalsize // 1024))
            if self.publisher is not None:
                inf.add_line('UninstallRegKeys', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}","Publisher",{},"{}"'.format(self.uninstall_id, 0x4000, self.publisher))

            inf.add_line('UninstallRegKeyDel', 'HKLM,"SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}"'.format(self.uninstall_id, self.uninstall_id))
            inf.add_line('UninstallRegKeyDel', 'HKLM,"SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{}"'.format(self.uninstall_id))

        # source disks
        for no, name in self.disks.items():
            inf.set_value('SourceDisksNames', no, '{},{}{}.CAB,0'.format(quoted_str(name), self.infname, no))
        inf.add_whole_section('SourceDisksFiles', self.cabfiles.source_disk_lines)

        # shortcut
        if self.shortcut is not None:
            inf.append_to_list_value('DefaultInstall', 'UpdateInis', 'ShortcutInstallIni')

            shortcut_desc = self.title or self.shortcut.rpartition('\\')[2]
            shortcut_dirid, shortcut_path = self.shortcut.split('\\', 1)
            shortcut_target = '"%{}%\\{}'.format(shortcut_dirid, quoted_str(shortcut_path)[1:])

            inf.add_line('ShortcutInstallIni', 'setup.ini,progman.groups,,"shortcutgrp1=."')
            inf.add_line('ShortcutInstallIni', 'setup.ini,shortcutgrp1,,""{}","""""{}""""""'.format(quoted_str(shortcut_desc), shortcut_target))


            if self.uninstall_id is not None:
                inf.append_to_list_value('DefaultUninstall', 'UpdateInis', 'ShortcutRemoveIni')

                inf.add_line('ShortcutRemoveIni', 'setup.ini,progman.groups,,"shortcutgrp1=."')
                inf.add_line('ShortcutRemoveIni', 'setup.ini,shortcutgrp1,,""{}""'.format(quoted_str(shortcut_desc)))


        # writing it out
        inf.write_to_file(os.path.join(self.outdir, self.infname + '.INF'))

        if self.copy_bootstrapper:
            with open(os.path.join(self.outdir, self.infname + '.EXE'), 'wb') as f:
                f.write(load_data(__package__, 'res', 'bootstrap32.exe'))

    def fake_floppy_disks(self):
        # Helper function for first stage of floppy distribution:
        # Fake some floppy entries which will later be replaced with real ones
        # after MAKECAB generates the floppy layout for us.
        # These entries must occupy at least as much space in the INF file
        # as the real entries, since MAKECAB uses every byte available
        # on the first floppy for the CAB files.
        count = self.cabfiles.totalsize // (1024 * 1440) + 2

        self.disks = collections.OrderedDict()
        for i in range(1, count+1):
            self.disks[str(i)] = '{} Disk {}'.format(self.title or self.infname, i)

        for f in self.cabfiles.out_files:
            self.cabfiles.set_file_disk(f, count)

    def fill_disks_from_makecab(self, info):
        self.disks = collections.OrderedDict()
        for no, title in info.disks.items():
            self.disks[no] = '{} Disk {}'.format(self.title or self.infname, no)

        for f, n in info.files.items():
            self.cabfiles.set_file_disk(f, n)

class SedFileBuilder:
    def __init__(self, sedfilename, exename):
        self.sedname = sedfilename
        self.exename = exename
        self.beginprompt = None
        self.endprompt = None
        self.title = None
        self.setupexe = None
        self.setupinf = None
        self._sourcedir = None
        self._files = []

    def add_files(self, d):
        self._sourcedir = d
        for i in os.listdir(d):
            if os.path.isfile(os.path.join(d, i)):
                self._files.append(i)

    def write_sed_file(self):
        sed = InfLikeFileBuilder()

        sed.add_whole_section('Version', [
                'Class=IEXPRESS',
                'SEDVersion=3',
            ])
        sed.add_whole_section('Options', [
                'PackagePurpose=InstallApp',
                'ShowInstallProgramWindow=0',
                'HideExtractAnimation=0',
                'UseLongFileName=0',
                'InsideCompressed=0',
                'CAB_FixedSize=0',
                'CAB_ResvCodeSigning=0',
                'RebootMode=I',
                'InstallPrompt=%InstallPrompt%',
                'DisplayLicense=%DisplayLicense%',
                'FinishMessage=%FinishMessage%',
                'TargetName=%TargetName%',
                'FriendlyName=%FriendlyName%',
                'AppLaunched=%AppLaunched%',
                'PostInstallCmd=%PostInstallCmd%',
                'AdminQuietInstCmd=%AdminQuietInstCmd%',
                'UserQuietInstCmd=%UserQuietInstCmd%',
                'SourceFiles=SourceFiles',
                '[Strings]',
                'InstallPrompt={}'.format(self.beginprompt or ''),
                'DisplayLicense=',
                'FinishMessage={}'.format(self.endprompt or ''),
                'TargetName={}'.format(self.exename),
                'FriendlyName={}'.format(self.title),
                'AppLaunched=',
                'PostInstallCmd=<None>',
                'AdminQuietInstCmd=',
                'UserQuietInstCmd=',
            ])

        if self.setupexe is not None: # bootstrapper
            sed.set_value('Options', 'AppLaunched', '{} /norestart'.format(self.setupexe))
            sed.set_value('Options', 'AdminQuietInstCmd', '{} /quiet /norestart'.format(self.setupexe))
            sed.set_value('Options', 'UserQuietInstCmd', '{} /quiet /norestart'.format(self.setupexe))
        else: # inf file
            sed.set_value('Options', 'AppLaunched', self.setupinf)

        sed.set_value('SourceFiles', 'SourceFiles0', self._sourcedir)

        for i in self._files:
            sed.set_value('SourceFiles0', i, '')

        sed.write_to_file(self.sedname)

class FloppyDdfFileBuilder:
    def __init__(self, cabdir):
        self.cabdir = cabdir
        self.infdir = '!!BUG!!'
        self.noncabfiles = []
        self.cabfiles = []
        self.title = 'SETUP'
        self.infname = 'SETUP'
        self.compress = True

    def load_files_from_infbuilder(self, infbuilder):
        self.noncabfiles = []
        self.cabfiles = []
        self.infdir = infbuilder.outdir
        self.title = infbuilder.title or 'SETUP'
        self.infname = infbuilder.infname

        if infbuilder.copy_bootstrapper:
            self.noncabfiles.append(infbuilder.infname + '.EXE')

        self.noncabfiles.append(infbuilder.infname + '.INF')

        for s in infbuilder.copysecs:
            for f in s.source_files:
                self.cabfiles.append(f)

    @property
    def ddf_file_name(self):
        return os.path.join(self.cabdir, 'SETUP.DDF')

    @property
    def inf_file_name(self):
        return os.path.join(self.cabdir, 'SETUP.INF')

    def write_ddf_file(self):
        l = []

        l.append('.OPTION EXPLICIT')
        l.append('.Set DiskLabelTemplate={} Disk *'.format(self.title))
        l.append('.Set CabinetNameTemplate={}*.CAB'.format(self.infname))
        l.append('.Set DiskDirectoryTemplate=Disk*')
        l.append('.Set MaxDiskSize=1.44M')
        l.append('.Set GenerateInf=ON')
        l.append('.Set InfFileName=SETUP.INF')
        l.append('.Set RptFileName=SETUP.RPT')
        l.append('.Set InfDiskHeader="[disk list]"')
        l.append('.Set InfDiskLineFormat="*disk#*=*label*"')
        l.append('.Set InfCabinetHeader="[cabinet list]"')
        l.append('.Set InfCabinetLineFormat="*cab#*=*disk#*,*cabfile*"')
        l.append('.Set InfFileHeader="[file list]"')
        l.append('.Set InfFileLineFormat="*file*=*disk#*"')
        l.append('.Set SourceDir="{}"'.format(self.infdir))
        l.append('')
        l.append('.Set Cabinet=Off')
        l.append('.Set Compress=Off')
        for f in self.noncabfiles:
            l.append(f)
        l.append('')
        l.append('.Set Cabinet=On')
        l.append('.Set FolderSizeThreshold=1000000')
        if self.compress:
            l.append('.Set Compress=On')
        else:
            l.append('.Set Compress=Off')
        for f in self.cabfiles:
            l.append(f)

        s = '\r\n'.join(l)

        if is_ascii(s):
            encoding = 'ASCII'
        else:
            encoding = 'utf-16'

        with open(self.ddf_file_name, 'w', encoding=encoding, newline='') as f:
            f.write(s)

class MakecabInfData:
    def __init__(self, inffilename):
        self.disks = collections.OrderedDict()
        self.files = collections.OrderedDict()

        cp = ConfigParser()
        cp.optionxform = str
        cp.read(inffilename)

        for num, title in cp.items('disk list'):
            self.disks[num] = title

        for f, num in cp.items('file list'):
            self.files[f] = num


def initialize_inf_builder(outdir, args):
    b = InfFileBuilder(outdir, args.short_inf_name)

    if args.with_uninstall is not None:
        b.uninstall_id = args.with_uninstall

    if args.publisher is not None:
        b.publisher = args.publisher

    if args.title is not None:
        b.title = args.title

    if args.shortcut is not None:
        b.shortcut = args.shortcut

    if args.with_bootstrapper:
        b.copy_bootstrapper = args.with_bootstrapper

    if args.advanced_inf:
        b.advanced_inf = args.advanced_inf

    b.installbeginprompt = 'Do you want to install {}?'.format(b.title or b.infname)
    b.installendprompt = '{} has been installed successfully.'.format(b.title or b.infname)

    b.add_source_files(args.source_dir)

    return b


ap = ArgumentParser()
ap.add_argument('--source-dir', required=True)
ap.add_argument('--make-filedist', metavar='OUTDIR')
ap.add_argument('--make-iexpress', metavar='OUTFILE.EXE')
ap.add_argument('--make-floppydist', metavar='OUTDIR')
ap.add_argument('--with-uninstall', metavar='ID')
ap.add_argument('--publisher')
ap.add_argument('--title')
ap.add_argument('--short-inf-name', default='SETUP')
ap.add_argument('--shortcut', metavar='TARGETFILE')
ap.add_argument('--with-bootstrapper', default=False, action='store_true')
ap.add_argument('--advanced-inf', default=False, action='store_true')
ap.add_argument('--iexpress-binary', metavar='IEXPRESS.EXE', default='IEXPRESS.EXE')
ap.add_argument('--no-cab-compress', action='store_true', default=False)

args = ap.parse_args()

if args.make_filedist is None and args.make_iexpress is None and args.make_floppydist is None:
    raise Exception('Need at least one of --make-filedist or --make-iexpress or --make-floppydist')

if args.make_filedist is not None:
    os.makedirs(args.make_filedist, exist_ok=True)

    b = initialize_inf_builder(args.make_filedist, args)

    b.write_inf_file()

if args.make_iexpress is not None:
    with tempfile.TemporaryDirectory() as tempdir:
        infdir = os.path.join(tempdir, 'files')
        os.makedirs(infdir, exist_ok=True)

        b = initialize_inf_builder(infdir, args)

        s = SedFileBuilder(os.path.join(tempdir, 'SETUP.SED'), args.make_iexpress)
        s.title = b.title or args.short_inf_name
        s.beginprompt = b.installbeginprompt
        b.installbeginprompt = None

        if args.with_bootstrapper:
            s.setupexe = b.infname + '.EXE'
        else:
            s.setupinf = b.infname + '.INF'

        if not args.with_bootstrapper or not args.advanced_inf:
            s.endprompt = b.installendprompt
            b.installendprompt = None

        b.write_inf_file()

        s.add_files(os.path.join(tempdir, 'files'))
        s.write_sed_file()

        subprocess.check_call([args.iexpress_binary, '/N', os.path.join(tempdir, 'SETUP.SED')])

if args.make_floppydist is not None:
    with tempfile.TemporaryDirectory() as tempdir:
        os.makedirs(args.make_floppydist, exist_ok=True)

        b = initialize_inf_builder(tempdir, args)
        b.fake_floppy_disks()
        b.write_inf_file()

        d = FloppyDdfFileBuilder(args.make_floppydist)
        d.compress = not args.no_cab_compress
        d.load_files_from_infbuilder(b)
        d.write_ddf_file()

        subprocess.check_call(['MAKECAB.EXE', '/F', os.path.join(d.ddf_file_name)], cwd=args.make_floppydist)

        b.fill_disks_from_makecab(MakecabInfData(d.inf_file_name))
        b.outdir = os.path.join(args.make_floppydist, 'Disk1')
        b.write_inf_file()
