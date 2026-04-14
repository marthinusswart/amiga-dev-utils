#!/usr/bin/env python3
"""
Create WHDLoad-compatible .lha archives from a directory of game files.
WHDLoad is a system for running Amiga games from hard drives.
"""

import argparse
import sys
import shutil
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)
from rich.panel import Panel
from rich import box


# Amiga filesystem limits
MAX_FILENAME_LENGTH = 30  # Amiga filesystem limit
MAX_PATH_LENGTH = 256  # Amiga path length limit

# Create console for rich output
console = Console()


def create_amiga_shell_script(output_lha_dir, game_name):
    """
    Create an Amiga shell script to package the game as LHA.
    """
    script_name = "make_lha.sh"
    script_path = output_lha_dir / script_name

    # Amiga shell script content
    script_content = f"""; Amiga shell script to create LHA archive
; Run this on your Amiga to package {game_name}

echo "Creating {game_name}.lha..."
cd ""
lha a ../{game_name}.lha {game_name}
if $RC EQ 0
  echo "Success! Created ../{game_name}.lha"
else
  echo "Error creating archive (code: $RC)"
endif
"""

    # Write the script
    with open(script_path, 'w', encoding='latin-1', newline='\n') as f:
        f.write(script_content)

    # Make it executable
    script_path.chmod(0o755)

    return script_path


def create_whdload_directory(staging_dir, game_name, output_dir):
    """
    Copy the WHDLoad directory structure to the output location.
    Returns the output LHA directory path.
    """
    try:
        game_dir = staging_dir / game_name
        output_lha_dir = output_dir / f"{game_name}-lha"
        output_game_dir = output_lha_dir / game_name

        # Remove existing output directory if it exists
        if output_lha_dir.exists():
            shutil.rmtree(output_lha_dir)

        # Create the LHA wrapper directory
        output_lha_dir.mkdir(parents=True, exist_ok=True)

        # Copy the entire game directory to output
        shutil.copytree(game_dir, output_game_dir)

        # Create the Amiga shell script
        script_path = create_amiga_shell_script(output_lha_dir, game_name)

        return output_lha_dir, script_path

    except Exception as e:
        console.print(f"[red]✗ Error creating WHDLoad directory:[/red] {e}")
        import traceback
        traceback.print_exc()
        return None, None


def get_directory_files(input_dir):
    """
    Get all files in the input directory recursively with their relative paths.
    Returns list of tuples: (absolute_path, relative_path, size)
    """
    files = []
    input_path = Path(input_dir)

    for file_path in input_path.rglob("*"):
        if file_path.is_file():
            relative_path = file_path.relative_to(input_path)
            size = file_path.stat().st_size
            files.append((str(file_path), str(relative_path), size))

    return sorted(files, key=lambda x: x[1])  # Sort by path for organized archive


def sanitize_amiga_filename(filename):
    """
    Sanitize a filename to be Amiga-compatible (max 30 characters).
    Preserves the extension and adds ~1 marker when truncated.
    """
    if len(filename) <= MAX_FILENAME_LENGTH:
        return filename

    # Split filename and extension
    name_parts = filename.rsplit(".", 1)
    if len(name_parts) == 2:
        name, ext = name_parts
        # Reserve space for extension + dot + ~1 marker
        max_name_len = MAX_FILENAME_LENGTH - len(ext) - 1 - 2  # -1 for dot, -2 for ~1
        if max_name_len > 0:
            return f"{name[:max_name_len]}~1.{ext}"

    # If no extension or can't preserve it, just truncate with ~1 at the end
    return filename[: MAX_FILENAME_LENGTH - 2] + "~1"


def sanitize_amiga_path(rel_path):
    """
    Sanitize a full relative path to be Amiga-compatible.
    Each component (directory and filename) must be <= 30 characters.
    """
    path_obj = Path(rel_path)
    parts = []

    for part in path_obj.parts[:-1]:  # Process directory names
        parts.append(sanitize_amiga_filename(part))

    # Process the filename (last part)
    if path_obj.parts:
        parts.append(sanitize_amiga_filename(path_obj.parts[-1]))

    return str(Path(*parts)) if parts else rel_path


def validate_files_for_whdload(files):
    """
    Validate that files are suitable for WHDLoad packaging.
    Returns (has_slave, has_info, exe_name) tuple.
    """
    has_slave = False
    has_info = False
    exe_name = None

    for _, rel_path, _ in files:
        path_lower = rel_path.lower()

        # Check for WHDLoad slave file
        if path_lower.endswith(".slave"):
            has_slave = True

        # Check for .info file
        if path_lower.endswith(".info"):
            has_info = True

        # Look for executable
        if path_lower.endswith(".exe") and exe_name is None:
            exe_name = Path(rel_path).name

    return has_slave, has_info, exe_name


def create_staging_directory(input_dir, game_name):
    """
    Create a temporary staging directory with proper WHDLoad structure.
    Returns the staging directory path.
    """
    staging_dir = Path(input_dir).parent / f".whdload_staging_{game_name}"

    # Clean up if it exists
    if staging_dir.exists():
        shutil.rmtree(staging_dir)

    # Create the game subdirectory (WHDLoad archives typically have a top-level folder)
    game_dir = staging_dir / game_name
    game_dir.mkdir(parents=True, exist_ok=True)

    return staging_dir, game_dir


def generate_slave_file(game_dir, game_name, exe_name=None):
    """
    Generate a basic WHDLoad slave file if one doesn't exist.
    Returns the slave file info tuple: (path, rel_path, size, was_generated)
    """
    # Find the main executable if not provided
    if exe_name is None:
        # Look for common executable patterns
        for pattern in ["*.exe", "*.EXE", "*[!.]*"]:
            exes = list(game_dir.glob(pattern))
            if exes:
                exe_name = exes[0].name
                break

    if exe_name is None:
        exe_name = "game"  # Default fallback

    slave_name = f"{game_name}.slave"
    slave_path = game_dir / slave_name

    # Basic WHDLoad slave template (M68k assembly)
    # This is a minimal slave that loads and runs the executable
    slave_template = f""";
; WHDLoad Slave for {game_name}
; Auto-generated by create_amiga_whdload.py
;

\t\tINCDIR\tIncludes:
\t\tINCLUDE\twhdload.i
\t\tINCLUDE\twhdmacros.i

\t\tIFD BARFLY
\t\tOPTIMAL
\t\tNOPPEX
\t\tENDC

CHIPMEMSIZE\tEQU\t$80000\t\t; 512KB chip mem
FASTMEMSIZE\tEQU\t$40000\t\t; 256KB fast mem
HRTMON\t\tEQU\t1

\t\tINCLUDE\tRTload.i

slv_Version\t=\t16
slv_Flags\t=\tWHDLF_NoError|WHDLF_Examine
slv_keyexit\t=\t$59\t\t\t; F10 to exit

\t\tINCLUDE\tdepack.i

slv_name\t\tdc.b\t'{game_name}',0
slv_copy\t\tdc.b\t'Auto-generated slave',0
slv_info\t\tdc.b\t'Install {exe_name}',0
\t\t\tdc.b\t0
slv_CurrentDir\tdc.b\t0

slv_config\t\tdc.b\t0

\t\tEVEN

_start\t\t
\t\tlea\t(_resload,pc),a1
\t\tmove.l\t(a5),a0
\t\tmove.l\t(a0),d0
\t\tbeq.b\t.skip
\t\tmove.l\td0,a0
\t\tmove.l\t(a0),d0
\t\tbeq.b\t.skip
\t\tmove.l\td0,a0
.skip
\t\tmove.l\t(resload_LoadFile,a0),d0
\t\tbeq.b\t.done
\t\tlea\t(exe_name,pc),a0
\t\tmove.l\t(resload_LoadFileDecrunch,a0),d0
\t\tbeq.b\t.done
\t\tjsr\t(a1)
.done
\t\trts

_resload\tdc.l\t0

exe_name\tdc.b\t'{exe_name}',0
\t\tEVEN

\t\tEND
"""

    # Write the slave file
    with open(slave_path, 'w', encoding='latin-1') as f:
        f.write(slave_template)

    size = slave_path.stat().st_size
    return (str(slave_path), slave_name, size, True)


def generate_info_file(game_dir, game_name):
    """
    Generate a basic .info file for Workbench integration.
    Returns the info file tuple: (path, rel_path, size, was_generated)
    """
    info_name = f"{game_name}.info"
    info_path = game_dir / info_name

    # Amiga .info file structure (Workbench icon)
    # This is a minimal binary format for a project icon
    # Format: https://wiki.amigaos.net/wiki/Icon_Library

    # Icon header
    info_data = bytearray()

    # Magic number for .info file (0xE310)
    info_data.extend(b'\xe3\x10')

    # Version (1.0)
    info_data.extend(b'\x00\x01')

    # Next gadget (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Left edge, Top edge, Width, Height
    info_data.extend(b'\x00\x00\x00\x00')  # Left = 0
    info_data.extend(b'\x00\x00')  # Top = 0
    info_data.extend(b'\x00\x50')  # Width = 80
    info_data.extend(b'\x00\x28')  # Height = 40

    # Flags (GFLG_GADGIMAGE | GFLG_GADGHBOX)
    info_data.extend(b'\x00\x05')

    # Activation flags
    info_data.extend(b'\x00\x01')

    # Gadget type (GTYP_BOOLGADGET)
    info_data.extend(b'\x00\x01')

    # Gadget render (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Select render (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Gadget text (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Mutual exclude, Special info, Gadget ID, User data
    info_data.extend(b'\x00\x00\x00\x00' * 4)

    # Icon type (WBPROJECT = 3)
    info_data.extend(b'\x00\x03')

    # Default tool (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Tool types (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Current X, Current Y
    info_data.extend(b'\x80\x00\x00\x00')  # NO_ICON_POSITION
    info_data.extend(b'\x80\x00\x00\x00')

    # Drawer data (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Tool window (NULL)
    info_data.extend(b'\x00\x00\x00\x00')

    # Stack size (4096)
    info_data.extend(b'\x00\x00\x10\x00')

    # Write the info file
    with open(info_path, 'wb') as f:
        f.write(info_data)

    size = info_path.stat().st_size
    return (str(info_path), info_name, size, True)


def copy_files_to_staging(files, game_dir):
    """
    Copy files to staging directory with sanitized names.
    Returns list of (original_rel_path, sanitized_rel_path, size, was_renamed) tuples.
    """
    copied_files = []

    for file_path, rel_path, size in files:
        # Sanitize the path to be Amiga-compatible
        amiga_path = sanitize_amiga_path(rel_path)
        target_path = game_dir / amiga_path

        # Create parent directories if needed
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Copy the file
        shutil.copy2(file_path, target_path)

        was_renamed = amiga_path != rel_path
        copied_files.append((rel_path, amiga_path, size, was_renamed))

    return copied_files


def create_lha_archive(staging_dir, game_name, output_path):
    """
    Create a .lha archive from the staging directory using lhafile library.
    """
    try:
        game_dir = staging_dir / game_name

        with lhafile.LhaFile(str(output_path), 'w') as lha:
            # Walk through the game directory and add all files
            for file_path in game_dir.rglob('*'):
                if file_path.is_file():
                    # Get the archive name (relative to staging_dir to include game_name folder)
                    arcname = str(file_path.relative_to(staging_dir))

                    # Add file to archive with forward slashes (Amiga path style)
                    arcname = arcname.replace('\\', '/')
                    lha.write(str(file_path), arcname)

        return True

    except Exception as e:
        console.print(f"[red]✗ Error creating LHA archive:[/red] {e}")
        return False


def display_file_table(copied_files, game_name):
    """
    Display a table of files that were added to the WHDLoad directory.
    """
    table = Table(
        title=f"[cyan]WHDLoad Directory[/cyan]: {game_name}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        width=100,
    )
    table.add_column("File", style="cyan", no_wrap=False)
    table.add_column("Size", justify="right", style="green")
    table.add_column("Status", justify="center", style="yellow")

    for orig_path, amiga_path, size, was_renamed in copied_files:
        # Format file display
        if orig_path == "[GENERATED]":
            display_path = f"[green]{game_name}/{amiga_path}[/green]"
            status = "[green]✓ generated[/green]"
            row_style = "on grey23"  # Gray background for generated files
        elif was_renamed:
            display_path = f"[amber]{orig_path}[/amber]\n[dim]→[/dim] [amber]{game_name}/{amiga_path}[/amber]"
            status = "[amber]✓ renamed[/amber]"
            row_style = "on grey23"  # Gray background for renamed files
        else:
            display_path = f"{game_name}/{orig_path}"
            status = "[green]✓[/green]"
            row_style = None

        size_str = f"{size:,} B"
        if size > 1024:
            size_str = f"{size/1024:.1f} KB"
        if size > 1024 * 1024:
            size_str = f"{size/1024/1024:.1f} MB"

        table.add_row(display_path, size_str, status, style=row_style)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(
        description="Create WHDLoad-compatible directories from a game directory"
    )
    parser.add_argument(
        "--amiga-in",
        required=True,
        help="Input directory containing game files (should include .slave file)",
    )
    parser.add_argument(
        "--amiga-out",
        required=True,
        help="Output directory where WHDLoad folder will be created",
    )
    parser.add_argument(
        "--name",
        help="Name for the archive (defaults to input directory name)",
    )

    args = parser.parse_args()

    input_dir = Path(args.amiga_in)
    output_dir = Path(args.amiga_out)
    game_name = args.name if args.name else input_dir.name

    # Print header
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Amiga WHDLoad Directory Creator[/bold cyan]\n"
            "[dim]Create WHDLoad-compatible directories (package as .lha on your Amiga)[/dim]",
            border_style="cyan",
        )
    )
    console.print()

    # Validate input directory
    if not input_dir.exists():
        console.print(
            f"[red]✗ Error:[/red] Input directory '{input_dir}' does not exist!"
        )
        sys.exit(1)

    if not input_dir.is_dir():
        console.print(f"[red]✗ Error:[/red] '{input_dir}' is not a directory!")
        sys.exit(1)

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get all files from input directory
    with console.status(
        f"[bold cyan]Scanning directory:[/bold cyan] {input_dir}", spinner="dots"
    ):
        files = get_directory_files(input_dir)

    if not files:
        console.print("[red]✗ Error:[/red] No files found in input directory!")
        sys.exit(1)

    total_size = sum(f[2] for f in files)

    # Validate files for WHDLoad
    has_slave, has_info, exe_name = validate_files_for_whdload(files)

    # Display scan results
    scan_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    scan_table.add_row("[cyan]Files Found:[/cyan]", f"[green]{len(files)}[/green]")
    scan_table.add_row(
        "[cyan]Total Size:[/cyan]",
        f"[green]{total_size:,} bytes ({total_size/1024/1024:.2f} MB)[/green]",
    )
    scan_table.add_row("[cyan]Directory Name:[/cyan]", f"[yellow]{game_name}[/yellow]")
    scan_table.add_row("[cyan]Input Directory:[/cyan]", f"[yellow]{input_dir}[/yellow]")
    scan_table.add_row(
        "[cyan]Output Directory:[/cyan]", f"[yellow]{output_dir}[/yellow]"
    )

    # Show what will be auto-generated
    if not has_slave:
        scan_table.add_row(
            "[cyan]Auto-generate:[/cyan]",
            f"[green]{game_name}.slave file[/green]"
        )
    if not has_info:
        scan_table.add_row(
            "[cyan]Auto-generate:[/cyan]",
            f"[green]{game_name}.info file[/green]"
        )

    console.print(scan_table)
    console.print()

    # Create staging directory
    try:
        with console.status(
            "[bold cyan]Preparing files for archiving...", spinner="dots"
        ):
            staging_dir, game_dir = create_staging_directory(input_dir, game_name)
            copied_files = copy_files_to_staging(files, game_dir)

            # Generate .slave file if it doesn't exist
            if not has_slave:
                console.print(
                    f"[green]✓ Generated:[/green] {game_name}.slave (WHDLoad slave file)"
                )
                slave_info = generate_slave_file(game_dir, game_name, exe_name)
                # Add to copied_files list for display
                copied_files.append((
                    "[GENERATED]",
                    slave_info[1],  # slave filename
                    slave_info[2],  # size
                    False  # not renamed
                ))

            # Generate .info file if it doesn't exist
            if not has_info:
                console.print(
                    f"[green]✓ Generated:[/green] {game_name}.info (Workbench icon)"
                )
                info_info = generate_info_file(game_dir, game_name)
                # Add to copied_files list for display
                copied_files.append((
                    "[GENERATED]",
                    info_info[1],  # info filename
                    info_info[2],  # size
                    False  # not renamed
                ))

        # Display file table
        display_file_table(copied_files, game_name)
        console.print()

        # Create WHDLoad directory in output location
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]Creating {game_name}-lha directory...", total=1
            )

            output_lha_dir, script_path = create_whdload_directory(staging_dir, game_name, output_dir)
            progress.advance(task)

        # Clean up staging directory
        shutil.rmtree(staging_dir)

        # Final summary
        console.print()
        if output_lha_dir and output_lha_dir.exists():
            output_game_dir = output_lha_dir / game_name
            final_size = sum(f.stat().st_size for f in output_game_dir.rglob('*') if f.is_file())

            console.print(
                Panel.fit(
                    f"[bold green]✓ Success![/bold green]\n"
                    f"Created WHDLoad directory: [yellow]{output_lha_dir}[/yellow]\n"
                    f"Game directory: [yellow]{game_name}/[/yellow]\n"
                    f"Total size: [green]{final_size:,} bytes ({final_size/1024/1024:.2f} MB)[/green]\n\n"
                    f"[bold cyan]On your Amiga:[/bold cyan]\n"
                    f"1. Copy [yellow]{output_lha_dir.name}/[/yellow] folder to your Amiga\n"
                    f"2. Run: [cyan]execute make_lha.sh[/cyan]\n"
                    f"3. This creates [yellow]{game_name}.lha[/yellow] in the parent directory",
                    border_style="green",
                )
            )
        else:
            console.print(
                Panel.fit(
                    f"[bold red]✗ Failed![/bold red]\n"
                    f"Could not create WHDLoad directory",
                    border_style="red",
                )
            )
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]✗ Error:[/red] {e}")
        # Clean up staging directory if it exists
        if 'staging_dir' in locals() and Path(staging_dir).exists():
            shutil.rmtree(staging_dir)
        sys.exit(1)


if __name__ == "__main__":
    main()
