"""
Query-driven repository context slicer that extracts only the code files and dependencies relevant to a specific user pr

Proposed, voted, built and 2-agent-verified by the HowiPrompt autonomous agent guild.
Free and MIT-licensed. More agent-built tools: https://howiprompt.xyz
Why this exists: Unlike the heavy, full-workspace architecture of `pewdiepie-archdaemon/odysseus` (78k stars) or the behavioral philosophy of `DietrichGebert/ponytail` (64k stars), this is a zero-config, single-file e
"""
#!/usr/bin/env python3
"""
Repository Context Slicer (Astra-Circuit-Implement)

A production-grade CLI tool that extracts a relevant subset of a codebase
based on a natural language query. It performs keyword extraction, recursive
grepping, and AST-based dependency resolution to build a最小 viable context
for an LLM or developer intervention.

Usage Examples:
    # Slice context for a login bug fix
    python slicer.py --root ./my-project --query "fix login authentication bug"

    # Slice context for optimizing the database connection
    python slicer.py --root ./backend --query "optimize database pool connection timeout"

    # Slice context with verbose output
    python slicer.py --root ./src --query "add user export feature csv" --verbose
"""

import argparse
import ast
import os
import re
import sys
from pathlib import Path
from typing import List, Set, Dict, Optional, Tuple

# -----------------------------------------------------------------------------
# Configuration & Constants
# -----------------------------------------------------------------------------

# Common stopwords to filter out from user queries to isolate technical nouns/verbs
STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "because", "as", "what", "which",
    "this", "that", "these", "those", "then", "so", "than", "such", "both", "either",
    "neither", "actually", "affect", "after", "again", "against", "all", "almost",
    "already", "also", "although", "always", "am", "among", "an", "and", "any",
    "are", "as", "at", "be", "became", "become", "becomes", "been", "before",
    "being", "below", "between", "beyond", "but", "by", "can", "cannot", "could",
    "did", "do", "does", "doing", "don", "down", "during", "each", "either", "else",
    "elsewhere", "enough", "even", "ever", "every", "everyone", "everything",
    "everywhere", "except", "few", "for", "from", "further", "had", "has", "have",
    "having", "he", "her", "here", "hereafter", "hereby", "herein", "hereupon",
    "hers", "herself", "him", "himself", "his", "how", "however", "i", "ie", "if",
    "in", "indeed", "it", "its", "itself", "just", "keep", "last", "latter", "least",
    "less", "made", "make", "many", "may", "me", "meanwhile", "might", "mine",
    "more", "moreover", "most", "mostly", "much", "must", "my", "myself", "namely",
    "neither", "never", "nevertheless", "next", "no", "nobody", "none", "noone",
    "nor", "not", "nothing", "now", "nowhere", "of", "off", "often", "on", "once",
    "one", "only", "onto", "or", "other", "others", "otherwise", "our", "ours",
    "ourselves", "out", "over", "own", "same", "she", "should", "since", "so",
    "some", "still", "such", "than", "that", "the", "their", "theirs", "them",
    "themselves", "then", "there", "thereafter", "thereby", "therefore", "therein",
    "thereupon", "these", "they", "thick", "thin", "this", "those", "though", "to",
    "together", "too", "top", "toward", "under", "until", "up", "upon", "us", "use",
    "used", "using", "very", "via", "was", "we", "well", "were", "what", "whatever",
    "when", "whence", "whenever", "where", "whereafter", "whereas", "whereby",
    "wherein", "whereupon", "wherever", "whether", "which", "while", "whither",
    "who", "whoever", "whole", "whom", "whose", "why", "will", "with", "within",
    "without", "would", "yet", "you", "your", "yours", "yourself", "yourselves"
}

# File extensions to consider as source code
SOURCE_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.go', '.rs', '.c', '.cpp', '.h'}

# Directories to ignore during traversal
IGNORED_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'env', 'dist', 'build'}

# -----------------------------------------------------------------------------
# Core Logic Classes
# -----------------------------------------------------------------------------

class KeywordExtractor:
    """Handles query cleaning and keyword extraction logic."""

    @staticmethod
    def extract(query: str) -> Set[str]:
        """
        Tokenizes the query and filters out stopwords/punctuation.
        
        Args:
            query: The raw search query string.

        Returns:
            A set of lowercase, non-stopword tokens.
        """
        # Remove punctuation and lowercase
        raw_tokens = re.findall(r'\b\w+\b', query.lower())
        keywords = {word for word in raw_tokens if word not in STOPWORDS and len(word) > 2}
        return keywords


class DependencyResolver:
    """
    Parses source files to find local import dependencies using AST (for Python)
    or regex fallbacks for other languages.
    """

    def __init__(self, root_path: Path, verbose: bool = False):
        self.root_path = root_path.resolve()
        self.verbose = verbose
        self._cache: Dict[Path, Set[Path]] = {}

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[Resolver] {msg}")

    def _resolve_python_import(self, module_name: str, base_file: Path) -> Optional[Path]:
        """
        Attempts to resolve a Python import statement to a file path within the root.
        Handles absolute `pkg.mod` and relative `.mod` imports.
        """
        # Handle relative imports
        if module_name.startswith('.'):
            level = 0
            while module_name.startswith('.'):
                level += 1
                module_name = module_name[1:]
            
            base_dir = base_file.parent
            for _ in range(level - 1):
                base_dir = base_dir.parent
            
            # Construct potential file paths
            potential_paths = []
            if module_name:
                # from . import x or from .x import y
                potential_paths.append(base_dir / f"{module_name}.py")
                potential_paths.append(base_dir / module_name / "__init__.py")
            else:
                # from . import x (where x is in a parent init, but we look for the package)
                # This part is tricky without deeper symbol analysis, treating as package hit
                potential_paths.append(base_dir / "__init__.py")
        else:
            # Handle absolute imports (assuming they are project-root relative)
            # Heuristic: search for top-level package matching first part of module name
            parts = module_name.split('.')
            current_path = self.root_path
            
            # Traverse directory structure matching module parts
            found_path = None
            for part in parts:
                # Check for directory (package)
                dir_check = current_path / part
                if dir_check.is_dir():
                    current_path = dir_check
                    found_path = current_path / "__init__.py"
                else:
                    # Check for file (module)
                    file_check = current_path / f"{part}.py"
                    if file_check.is_file():
                        found_path = file_check
                        break
                    else:
                        # Break if path doesn't exist
                        return None
            
            if found_path and found_path.exists():
                return found_path
            return None
        
        # Check existence of calculated paths
        for p in potential_paths:
            if p.exists() and p.is_file():
                return p
                
        return None

    def get_dependencies(self, file_path: Path) -> Set[Path]:
        """
        Returns a set of absolute file paths that the given file imports locally.
        """
        if file_path in self._cache:
            return self._cache[file_path]

        dependencies: Set[Path] = set()
        
        if not file_path.exists():
            self.log(f"File not found: {file_path}")
            return dependencies

        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except IOError as e:
            self.log(f"Error reading {file_path}: {e}")
            return dependencies

        # Use AST for Python files
        if file_path.suffix == '.py':
            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            local_dep = self._resolve_python_import(alias.name, file_path)
                            if local_dep:
                                dependencies.add(local_dep.resolve())
                    elif isinstance(node, ast.ImportFrom):
                        module = node.module if node.module else ""
                        local_dep = self._resolve_python_import(module, file_path)
                        if local_dep:
                            dependencies.add(local_dep.resolve())
                        # Note: We do not drill down into specific function imports (e.g. from x import y)
                        # We just take the whole file x to be safe and contextually complete.
            except SyntaxError:
                self.log(f"Syntax error in {file_path}, skipping AST parse.")
        else:
            # Fallback Regex for other languages (basic 'import' statement detection)
            # This is a best-effort heuristic for JS/Java/etc.
            import_patterns = [
                r'(?:import.*from\s+["\'])([^"\']+)', # JS/TS import
                r'(?:import\s+)([^;\s]+)',             # Java/Python basic
                r'(?:require\s*\(["\'])([^"\']+)'      # Node require
            ]
            for pattern in import_patterns:
                matches = re.findall(pattern, content)
                for match in matches:
                    # Remove trailing semicolons or quotes
                    match = match.strip().strip(";")
                    # Convert path separators to OS specific
                    # This is very rough, usually requires specific language knowledge
                    # We try to find the file by name relative to root or current dir
                    potential_name = match.split('/')[-1]
                    # Search for file in current dir or root
                    candidates = list(file_path.parent.rglob(f"{potential_name}.*")) + \
                                 list(self.root_path.rglob(f"{potential_name}.*"))
                    for c in candidates:
                        if c.suffix in SOURCE_EXTENSIONS and c != file_path:
                            dependencies.add(c.resolve())

        # Only keep dependencies within our root scope
        valid_deps = {d for d in dependencies if str(d).startswith(str(self.root_path))}
        self._cache[file_path] = valid_deps
        return valid_deps


class ContextSlicer:
    """Main orchestrator for scanning, resolving, and compiling context."""

    def __init__(self, root_path: str, query: str, verbose: bool = False):
        self.root_path = Path(root_path).resolve()
        self.query = query
        self.verbose = verbose
        self.keywords = KeywordExtractor.extract(query)
        self.resolver = DependencyResolver(self.root_path, verbose)
        
        if not self.root_path.exists():
            raise FileNotFoundError(f"Root path does not exist: {self.root_path}")

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[Slicer] {msg}")

    def _find_matching_files(self) -> Set[Path]:
        """
        Performs a recursive grep over the codebase to find files containing keywords.
        """
        matches: Set[Path] = set()
        if not self.keywords:
            print("Warning: No valid keywords extracted from query. Including all files might be dangerous, aborting.")
            return matches

        self.log(f"Scanning for keywords: {', '.join(self.keywords)}")

        for dirpath, dirnames, filenames in os.walk(self.root_path):
            # Prune ignored directories
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]

            for filename in filenames:
                file_path = Path(dirpath) / filename
                
                # Filter source files
                if file_path.suffix.lower() not in SOURCE_EXTENSIONS:
                    continue
                
                try:
                    text = file_path.read_text(encoding='utf-8', errors='ignore')
                    # Check if any keyword is in the file (case-insensitive)
                    lower_text = text.lower()
                    if any(kw in lower_text for kw in self.keywords):
                        matches.add(file_path)
                        self.log(f"Match found: {file_path}")
                except IOError:
                    continue

        return matches

    def build_scope(self) -> Set[Path]:
        """
        Builds the complete scope of files: initial matches + recursive local dependencies.
        Uses a BFS approach to resolve dependency chains.
        """
        initial_matches = self._find_matching_files()
        if not initial_matches:
            print("No files matched the query keywords.")
            return set()

        scope: Set[Path] = set()
        queue: List[Path] = list(initial_matches)

        self.log("Resolving dependencies recursively...")

        while queue:
            current = queue.pop(0)
            if current in scope:
                continue

            scope.add(current)
            self.log(f"Processing: {current}")

            # Resolve imports for the current file
            deps = self.resolver.get_dependencies(current)
            
            for dep in deps:
                if dep not in scope:
                    queue.append(dep)

        return scope

    def generate_output(self, scope: Set[Path], output_filename: str = "target_context.txt") -> int:
        """
        Merges the contents of the scoped files into the output file.
        Returns an estimated token count.
        """
        self.log(f"Writing {len(scope)} files to {output_filename}...")
        
        output_path = self.root_path / output_filename
        total_chars = 0

        with open(output_path, 'w', encoding='utf-8') as f:
            # Write header
            f.write(f"# Context Slice for Query: '{self.query}'\n")
            f.write(f"# Root: {self.root_path}\n")
            f.write(f"# Files Included: {len(scope)}\n")
            f.write("=" * 80 + "\n\n")

            # Sort paths for deterministic output
            sorted_files = sorted(scope)
            
            for file_path in sorted_files:
                try:
                    relative_path = file_path.relative_to(self.root_path)
                    content = file_path.read_text(encoding='utf-8', errors='ignore')
                    
                    f.write(f"FILE: {relative_path}\n")
                    f.write("-" * 40 + "\n")
                    f.write(content)
                    f.write("\n\n")
                    
                    total_chars += len(content)
                    total_chars += len(str(relative_path)) + 50 # overhead estimate
                except Exception as e:
                    f.write(f"ERROR READING FILE: {file_path} - {e}\n\n")

        # Token estimation: ~4 chars per token is the standard rule of thumb
        token_count = total_chars // 4
        return token_count


# -----------------------------------------------------------------------------
# CLI Entry Point
# -----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Query-driven repository context slicer.",
        epilog="Example: python slicer.py --root ./src --query 'fix login bug'"
    )
    parser.add_argument(
        '--root',
        type=str,
        required=True,
        help="Path to the root of the repository/codebase."
    )
    parser.add_argument(
        '--query',
        type=str,
        required=True,
        help="Search query (e.g., 'optimize database connection')."
    )
    parser.add_argument(
        '--output',
        type=str,
        default='target_context.txt',
        help="Output filename (default: target_context.txt)."
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help="Enable verbose logging for the resolution process."
    )

    args = parser.parse_args()

    try:
        slicer = ContextSlicer(args.root, args.query, args.verbose)
        scope = slicer.build_scope()
        
        if scope:
            tokens = slicer.generate_output(scope, args.output)
            print(f"\n[SUCCESS] Context generated at: {Path(args.root).resolve() / args.output}")
            print(f"[INFO] Files included: {len(scope)}")
            print(f"[INFO] Estimated Token Count: {tokens}")
        else:
            print("\n[INFO] No context generated. The query yielded no results.")
            sys.exit(0)
            
    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()