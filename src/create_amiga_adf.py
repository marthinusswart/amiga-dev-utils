#!/usr/bin/env python3
"""
Create Amiga .adf floppy disk images from a directory of files.
Automatically splits content across multiple disks if needed.
"""

import argparse
import sys
from pathlib import Path
from amitools.fs.ADFSVolume import ADFSVolume
from amitools.fs.blkdev.BlkDevFactory import BlkDevFactory
from amitools.fs.FSError import FSError
from amitools.fs.FSString import FSString
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
from rich.text import Text


# Amiga DD floppy specs
FLOPPY_SIZE_BYTES = 901120  # 880 KB (80 tracks * 2 sides * 11 sectors * 512 bytes)
USABLE_SPACE_BYTES = 800 * 1024  # ~800 KB usable after filesystem overhead
MAX_FILENAME_LENGTH = 30  # Amiga filesystem limit

# Create console for rich output
console = Console()


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

    return sorted(
        files, key=lambda x: x[2], reverse=True
    )  # Sort by size, largest first


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


def distribute_files_to_disks(files, max_size):
    """
    Distribute files across multiple disks using a simple bin-packing algorithm.
    Returns list of lists, where each inner list contains files for one disk.
    """
    disks = []
    current_disk = []
    current_size = 0

    for file_info in files:
        _, rel_path, size = file_info

        # Add some overhead for directory entries and metadata (roughly 512 bytes per file)
        size_with_overhead = size + 512

        if size_with_overhead > max_size:
            console.print(
                f"[yellow]⚠[/yellow] Warning: File '{rel_path}' ({size:,} bytes) is too large for a single floppy!"
            )
            continue

        if current_size + size_with_overhead <= max_size:
            current_disk.append(file_info)
            current_size += size_with_overhead
        else:
            # Start a new disk
            if current_disk:
                disks.append(current_disk)
            current_disk = [file_info]
            current_size = size_with_overhead

    # Add the last disk
    if current_disk:
        disks.append(current_disk)

    return disks


def create_adf_image(output_path, volume_name, files, disk_num, total_disks):
    """
    Create an .adf image and add files to it.
    """
    blkdev = None
    adf = None

    # Create a table for this disk
    table = Table(
        title=f"[cyan]Disk {disk_num}/{total_disks}[/cyan]: {output_path.name}",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        width=100,
    )
    table.add_column("File", style="cyan", no_wrap=False)
    table.add_column("Size", justify="right", style="green")
    table.add_column("Status", justify="center", style="yellow")

    try:
        # Create a block device file
        blkdev_factory = BlkDevFactory()
        blkdev = blkdev_factory.create(str(output_path), force=True)

        # Create the ADF volume on the block device (OFS format)
        adf = ADFSVolume(blkdev)
        fs_volume_name = FSString(volume_name)
        adf.create(fs_volume_name, is_ffs=False)
        adf.open()

        # Add each file to the ADF
        for file_path, rel_path, size in files:
            # Sanitize the path to be Amiga-compatible
            amiga_path = sanitize_amiga_path(rel_path)

            # Create directories if needed
            amiga_path_obj = Path(amiga_path)
            if len(amiga_path_obj.parts) > 1:
                # Create parent directories
                current_path = ""
                for part in amiga_path_obj.parts[:-1]:
                    current_path = f"{current_path}/{part}" if current_path else part
                    try:
                        adf.create_dir(FSString(current_path))
                    except FSError:
                        # Directory might already exist
                        pass

            # Add the file
            with open(file_path, "rb") as f:
                file_data = f.read()

            adf.write_file(file_data, FSString(str(amiga_path)))

            # Format file display
            if amiga_path != rel_path:
                display_path = f"[amber]{rel_path}[/amber]\n[dim]→[/dim] [amber]{amiga_path}[/amber]"
                status = "[amber]✓ renamed[/amber]"
                row_style = "on grey23"  # Gray background for renamed files
            else:
                display_path = rel_path
                status = "[green]✓[/green]"
                row_style = None

            size_str = f"{size:,} B"
            if size > 1024:
                size_str = f"{size/1024:.1f} KB"

            table.add_row(display_path, size_str, status, style=row_style)

        adf.close()
        blkdev.close()

        console.print(table)
        return True

    except Exception as e:
        console.print(f"[red]✗ Error creating ADF:[/red] {e}")
        if adf:
            try:
                adf.close()
            except:
                pass
        if blkdev:
            try:
                blkdev.close()
            except:
                pass
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Create Amiga .adf floppy disk images from a directory"
    )
    parser.add_argument(
        "--amiga-in",
        required=True,
        help="Input directory containing files to add to the floppy",
    )
    parser.add_argument(
        "--amiga-out",
        required=True,
        help="Output directory where .adf files will be created",
    )

    args = parser.parse_args()

    input_dir = Path(args.amiga_in)
    output_dir = Path(args.amiga_out)

    # Print header
    console.print()
    console.print(
        Panel.fit(
            "[bold cyan]Amiga ADF Creator[/bold cyan]\n"
            "[dim]Create bootable Amiga floppy disk images[/dim]",
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

    # Get base name for the ADF files from input directory name
    base_name = input_dir.name

    # Get all files from input directory
    with console.status(
        f"[bold cyan]Scanning directory:[/bold cyan] {input_dir}", spinner="dots"
    ):
        files = get_directory_files(input_dir)

    if not files:
        console.print("[red]✗ Error:[/red] No files found in input directory!")
        sys.exit(1)

    total_size = sum(f[2] for f in files)

    # Display scan results
    scan_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    scan_table.add_row("[cyan]Files Found:[/cyan]", f"[green]{len(files)}[/green]")
    scan_table.add_row(
        "[cyan]Total Size:[/cyan]",
        f"[green]{total_size:,} bytes ({total_size/1024/1024:.2f} MB)[/green]",
    )
    scan_table.add_row("[cyan]Input Directory:[/cyan]", f"[yellow]{input_dir}[/yellow]")
    scan_table.add_row(
        "[cyan]Output Directory:[/cyan]", f"[yellow]{output_dir}[/yellow]"
    )
    console.print(scan_table)
    console.print()

    # Distribute files across disks
    with console.status("[bold cyan]Planning disk layout...", spinner="dots"):
        disks = distribute_files_to_disks(files, USABLE_SPACE_BYTES)
        num_disks = len(disks)

    # Display disk plan
    plan_table = Table(
        title="[bold cyan]Disk Distribution Plan[/bold cyan]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    plan_table.add_column("Disk", style="cyan", justify="center")
    plan_table.add_column("Files", justify="right", style="yellow")
    plan_table.add_column("Size", justify="right", style="green")
    plan_table.add_column("Usage", justify="right", style="blue")

    for i, disk_files in enumerate(disks, start=1):
        disk_size = sum(f[2] for f in disk_files)
        usage_pct = (disk_size / USABLE_SPACE_BYTES) * 100
        disk_name = f"Disk {i}" if num_disks > 1 else "Single Disk"
        plan_table.add_row(
            disk_name,
            str(len(disk_files)),
            f"{disk_size:,} B ({disk_size/1024:.1f} KB)",
            f"{usage_pct:.1f}%",
        )

    console.print(plan_table)
    console.print()

    # Create ADF images with progress
    success_count = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        overall_task = progress.add_task(
            f"[cyan]Creating {num_disks} ADF image(s)...", total=num_disks
        )

        for disk_num, disk_files in enumerate(disks, start=1):
            if num_disks > 1:
                adf_name = f"{base_name}_disk{disk_num}.adf"
                volume_name = f"{base_name[:8]}{disk_num}"
            else:
                adf_name = f"{base_name}.adf"
                volume_name = base_name[:30]

            output_path = output_dir / adf_name

            progress.update(overall_task, description=f"[cyan]Creating {adf_name}...")

            if create_adf_image(
                output_path, volume_name, disk_files, disk_num, num_disks
            ):
                success_count += 1
                console.print(
                    f"[green]✓ Created:[/green] [yellow]{output_path}[/yellow]\n"
                )
            else:
                console.print(f"[red]✗ Failed to create:[/red] {output_path}\n")
                sys.exit(1)

            progress.advance(overall_task)

    # Final summary
    console.print()
    if success_count == num_disks:
        console.print(
            Panel.fit(
                f"[bold green]✓ Success![/bold green]\n"
                f"Created {num_disks} ADF image(s) in [yellow]{output_dir}[/yellow]",
                border_style="green",
            )
        )
    else:
        console.print(
            Panel.fit(
                f"[bold red]✗ Failed![/bold red]\n"
                f"Only {success_count}/{num_disks} disk(s) created successfully",
                border_style="red",
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
