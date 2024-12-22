import argparse
import subprocess
import os
import shutil
import re
import time
from datetime import datetime
import zipfile
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CommandExecutionError(Exception):
    pass

def execute_command(command: list[str], log_output=True):
    logger.info(f'Executing command: "{" ".join(command)}"')
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = process.communicate()
    
    if log_output:
        log_command_output(process.pid, out, err)
    
    if process.returncode != 0:
        raise CommandExecutionError(f"Command failed: {command}. Exit code: {process.returncode}")
    
    return out.decode('utf-8'), err.decode('utf-8')

def log_command_output(pid, out, err):
    stdout_log = f"{pid}_stdout.log"
    stderr_log = f"{pid}_stderr.log"
    
    with open(stdout_log, "w") as f:
        f.write(out.decode('utf-8'))
    with open(stderr_log, "w") as f:
        f.write(err.decode('utf-8'))
    
    logger.info(f"Output log files: {stdout_log}, {stderr_log}")

def check_file(filename):
    exists = os.path.exists(filename)
    logger.info(f"Checking file {'exists' if exists else 'does not exist'}: {filename}")
    return exists

def match_and_get(regex: str, pattern: str):
    matched = re.search(regex, pattern)
    if not matched:
        raise ValueError(f'Failed to match: for pattern: {pattern} regex: {regex}')
    return matched.group(1)

def print_dictinfo(info: dict[str, str]):
    logger.info('=' * 40)
    for k, v in info.items():
        logger.info(f"{k}={v}")
    logger.info('=' * 40)

def zip_files(zipfilename: str, files: list[str]):
    logger.info(f"Zipping {len(files)} files to {zipfilename}...")
    with zipfile.ZipFile(zipfilename, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for f in files:
            zf.write(f)
    logger.info("Zipping completed successfully")

class CompilerClang:
    @staticmethod
    def test_executable():
        try:
            execute_command(['./toolchain/bin/clang', '-v'])
        except CommandExecutionError as e:
            logger.error("Failed to execute clang, something went wrong")
            raise e
    
    @staticmethod
    def get_version():
        clangversionRegex = r"(.*?clang version \d+(\.\d+)*).*"
        _, tcversion = execute_command(['./toolchain/bin/clang', '-v'], log_output=False)
        return match_and_get(clangversionRegex, tcversion)

def parse_arguments():
    parser = argparse.ArgumentParser(description="Build Grass Kernel with specified arguments")
    
    parser.add_argument('--oneui', action='store_true', help="OneUI variant")
    parser.add_argument('--permissive', action='store_true', help="Permissive SELinux")
    parser.add_argument('--target', type=str, required=True, help="Target device (a51/m21/...)")
    parser.add_argument('--no-ksu', action='store_true', help="Don't include KernelSU support in kernel")
    parser.add_argument('--allow-dirty', action='store_true', help="Allow dirty build")

    args = parser.parse_args()
    
    if args.target not in ['a51', 'm21', 'm31', 'm31s', 'f41', 'gta4xl', 'gta4xlwifi']:
        raise ValueError("Please specify a valid target: a51/m21/m31/m31s/f41/gta4xl/gta4xlwifi")
    
    return args

def setup_environment():
    if not check_file("toolchain"):
        raise FileNotFoundError(f"Please make toolchain available at {os.getcwd()}")

    execute_command(['git', 'submodule', 'update', '--init'])
    
    CompilerClang.test_executable()
    
    tcPath = os.path.join(os.getcwd(), 'toolchain', 'bin')
    if tcPath not in os.environ['PATH'].split(os.pathsep):
        os.environ["PATH"] = tcPath + ':' + os.environ["PATH"]

def build_kernel(args):
    variantStr = 'OneUI' if args.oneui else 'AOSP'
    
    print_dictinfo({
        'TARGET_KERNEL': 'SN',
        'TARGET_VARIANT': variantStr,
        'TARGET_DEVICE': args.target,
        'TARGET_INCLUDES_KSU': not args.no_ksu,
        'TARGET_USES_LLVM': True,
        'TOOLCHAIN': CompilerClang.get_version(),
    })
    
    outDir = 'out'
    if os.path.exists(outDir) and not args.allow_dirty:
        logger.info('Make clean...')
        shutil.rmtree(outDir)

    common_flags = [
        'CROSS_COMPILE=aarch64-linux-gnu-', 'CC=clang', 'LD=ld.lld', 
        'AS=llvm-as', 'AR=llvm-ar', 'OBJDUMP=llvm-objdump', 
        'READELF=llvm-readelf', 'NM=llvm-nm', 'OBJCOPY=llvm-objcopy', 
        'ARCH=arm64', f'-j{os.cpu_count()}'
    ]
    
    make_common = ['make', 'O=out', 'LLVM=1', f'-j{os.cpu_count()}'] + common_flags
    defconfigs = [f'exynos9611-{args.target}_defconfig']
    if not args.no_ksu:
        defconfigs.append('ksu.config')
    if args.oneui:
        defconfigs.append('oneui.config')
    if args.permissive:
        defconfigs.append('permissive.config')
    defconfigs = [i for i in defconfigs]
    
    make_defconfig = make_common + defconfigs
    
    start_time = datetime.now()
    logger.info('Make defconfig...')
    execute_command(make_defconfig)
    logger.info('Make kernel...')
    execute_command(make_common)
    logger.info('Kernel build completed')
    build_time = datetime.now() - start_time
    
    return build_time

def package_kernel(args, build_time):
    outDir = 'out'
    variantStr = 'OneUI' if args.oneui else 'AOSP'
    
    with open(os.path.join(outDir, 'include', 'generated', 'utsrelease.h')) as f:
        kver = match_and_get(r'"([^"]+)"', f.read())
    
    shutil.copyfile('out/arch/arm64/boot/Image', 'AnyKernel3/Image')
    zipname = f'SN_{args.target}_{variantStr}_{datetime.today().strftime("%Y-%m-%d")}.zip'
    
    os.chdir('AnyKernel3/')
    zip_files(zipname, [
        'Image', 
        'META-INF/com/google/android/update-binary',
        'META-INF/com/google/android/updater-script',
        'tools/ak3-core.sh',
        'tools/busybox',
        'tools/magiskboot',
        'anykernel.sh'
    ])
    
    newZipName = os.path.join(os.getcwd(), '..', zipname)
    try:
        os.remove(newZipName)
    except FileNotFoundError:
        pass
    shutil.move(zipname, newZipName)
    os.chdir('..')
    
    print_dictinfo({
        'OUT_ZIPNAME': zipname,
        'KERNEL_VERSION': kver,
        'BUILD_TIME': f"{build_time.total_seconds():.2f} seconds"
    })

def main():
    try:
        args = parse_arguments()
        setup_environment()
        build_time = build_kernel(args)
        package_kernel(args, build_time)
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
        raise

if __name__ == '__main__':
    main()
