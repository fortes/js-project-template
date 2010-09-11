import os
import sys
import json
from paver.easy import *
from paver.path import path

HOME_DIR = path(__file__).dirname().abspath()

options(
    home_dir = HOME_DIR,
    build_dir = HOME_DIR / 'build',
    src_dir = HOME_DIR / 'src',
    test_dir = HOME_DIR / 'test',
    tmp_dir = HOME_DIR / '.tmp',
    closure_library_dir = HOME_DIR / '../closure-library-read-only',
    closure_compiler = HOME_DIR / '../closure-compiler-read-only/build/compiler.jar',
    closure_lint = 'gjslint',
    closure_fix_lint = 'fixjsstyle',
)

# Run options again to use variables from previous declaration
options(
    modules_file = options.src_dir / 'modules.json',
    externs_dir = options.src_dir / 'externs',
    calcdeps = options.closure_library_dir / 'closure/bin/calcdeps.py',
    depswriter = options.closure_library_dir / 'closure/bin/build/depswriter.py',
)

# Closure compiler options
options(
    compiler_errors = [
        '',
        'accessControls',
        'checkRegExp',
        'checkTypes',
        'checkVars',
        'deprecated',
        'fileoverviewTags',
        'invalidCasts',
        'missingProperties',
        'undefinedVars',
        'visibility'
    ],
    compiler_warnings = [
        '',
        'nonStandardJsDocs',
        'strictModuleDepCheck',
        'unknownDefines',
    ],
    compiler_flags = [
        '--warning_level=VERBOSE',
        '--summary_detail_level=3',
    ]
)

def check_requirements():
    """Make sure all system requirements are met"""
    if not options.closure_library_dir.isdir():
        raise BuildFailure("Closure library not found")

    if not options.closure_compiler.isfile():
        raise BuildFailure("Closure compiler not found")

# Run requirements test no matter what
check_requirements()

@task
def clean():
    """Cleans up the project directory"""
    options.build_dir.rmtree()
    options.tmp_dir.rmtree()

@task
def default():
    """Write out dependency file in order to test non-compiled scripts"""
    js_files = options.src_dir.files('*.js')
    sh('python %s -i %s -p % s -d %s -o deps --output_file %s' % (
        options.calcdeps,
        ' -i '.join(js_files),
        options.src_dir,
        options.closure_library_dir,
        options.test_dir / 'deps.js'
    ))

@task
def lint():
    """Run Google's style checker on source files"""
    if not options.tmp_dir.isdir():
        options.tmp_dir.makedirs()

    last_run = None
    lint_flag = options.tmp_dir / 'lint'
    if lint_flag.exists():
        last_run = (options.tmp_dir / 'lint').mtime

    lint_flag.touch()

    for file in options.src_dir.walkfiles('*.js'):
        if not last_run or (last_run < file.mtime):
            sh('%s %s' % (
                options.closure_lint,
                file
            ))

@task
def fix_lint():
    """Run Closure lint fixer on all source files"""
    for file in options.src_dir.walkfiles('*.js'):
        sh('%s %s' % (
            options.closure_fix_lint,
            file
        ))

def get_dependency_list(js_files):
    """Returns list of dependency filenames in compilation order"""
    list = sh('python %s -i %s -p % s -p %s -o list -c %s' % (
        options.calcdeps,
        ' -i '.join(js_files),
        options.src_dir,
        options.closure_library_dir,
        options.closure_compiler,
    ), capture=True)

    # Remove duplicate lines
    found = set()
    files = []
    for line in list.splitlines():
        # Only want filenames, ignore the other stuff in output
        if not line or line[0] != '/' or line in found:
            continue
        found.add(line)
        files.append(line)

    return files

@task
@consume_args
def compile(args):
    """Compile and minimize files using Closure Compiler"""
    if not options.build_dir.isdir():
        options.build_dir.makedirs()

    # Whether we should compile to a single file instead of modules
    is_single = '--single' in args or not options.modules_file.isfile()

    compiler_flags = [
        '--compilation_level=ADVANCED_OPTIMIZATIONS',
        ' --jscomp_error '.join(options.compiler_errors),
        ' --jscomp_warning '.join(options.compiler_warnings),
        # Don't leak global variables
        """--output_wrapper '(function(){"use strict";%output%}());'"""
    ]

    # Make pretty output for debug mode
    if '--debug' in args:
        compiler_flags.append('--debug=true')
        compiler_flags.append('--formatting=PRETTY_PRINT')
        compiler_flags.append('--formatting=PRINT_INPUT_DELIMITER')

    # Externs definitions
    if options.externs_dir.isdir():
        for extern in options.externs_dir.files('*.js'):
            compiler_flags.append('--externs=%s' % extern)

    js_files = options.src_dir.files('*.js')

    # Compile into a single file or modules?
    if is_single:
        file_list = get_dependency_list(js_files)
        compiler_flags.append('--js %s' % ' --js '.join(file_list))
        compiler_flags.append('--js_output_file=%s' % (options.build_dir / 'all.js'))

        # Let code know modules are not being used
        compiler_flags.append('--define="USE_MODULES=false"')
    else:
        # Calculate the module info
        module_info = json.loads(open(options.modules_file).read())
        # TODO: Refactor and make this sane. Do a topological sort on dependency
        # graph so it doesn't have to be manually specified
        compile_order = module_info['order']
        file_list = []
        file_counts = []
        for js_file in compile_order:
            file_count = 0
            deps = get_dependency_list([options.src_dir / js_file])
            # Cannot include a file more than once
            for dep in deps:
                if dep not in file_list:
                    file_list.append(dep)
                    file_count += 1
            file_counts.append(file_count)

        compiler_flags.append('--module_output_path_prefix %s' % (options.build_dir / ''))
        compiler_flags.append('--js %s' % ' --js '.join(file_list))
        for i, js_file in enumerate(compile_order):
            dependencies = ''
            if len(module_info['dependencies'][js_file]):
                dependencies = ':%s' % ','.join([name[:-3] for name in module_info['dependencies'][js_file]])

            compiler_flags.append('--module %s:%s%s' % (
                js_file[:-3],
                file_counts[i],
                dependencies
            ))

    # Run the compilation
    sh('java -jar %s %s' % (
        options.closure_compiler,
        ' '.join(compiler_flags),
    ))

    size()

@task
def size():
    """Display the size of compiled files"""
    sizes = {}
    max_name_width = 0
    max_size_width = 0
    for js_file in options.build_dir.files('*.js'):
        name, size = js_file.basename(), js_file.size
        sizes[name] = size
        max_name_width = max(max_name_width, len(name))
        max_size_width = max(max_size_width, len(str(size)))

    format_string = ' %%%gs:  %%%gg bytes' % (max_name_width, max_size_width)
    items = sizes.items()
    items.sort()
    for name, size in items:
        print(format_string % (name, size))
