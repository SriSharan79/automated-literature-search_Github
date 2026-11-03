#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys

sys.path.extend([
    r'01_Index_cretion',
    r'02_Response_Generation',
    r'03_Data_Sorting',
    r'04_Rest_API',
    r'00_Prompt_Variables',
    r'05_LogingResults',
    r'03_Data_Sorting\Data_Analysis_on_Logged_results'
])
from dataclasses import dataclass
import os
# from Prompts_Variables import*
import re
import sys
import textwrap
import requests
import datetime
import argparse
from pathlib import Path
from typing import List, Tuple, Optional

from alr.common.llm_utils import llm_call

usecase=""""
A java plugin for git based synchronization of sysml v2 system model that was modeled in Cameo system modeler
"""

OSIRIS_Usecase="""
OSIRIS is a Java plugin for Cameo Systems Modeler that supports Functional Hazard Assessment (FHA) for aircraft systems by using LLMs to generate and refine failure conditions from SysML models and safety context.
"""

sys_prompt_java_commentor= f"""
You are an expert senior Java developer. 
{usecase}
Your task is to write or improve Javadoc comments for pulgin Java source code so that it complies with the java code commenting standards and is suitable for long-term, commercial-grade maintenance.
follow these instructions:
- make sure not include any java code in the responses 
- don't porovide any Javadoc commenting structures in the text generated 
- just provide the data requested in general text form as short as possible as per instructions mentioned
"""

# --------------------------------------------------------------------------- #
#                               Regex helpers
# --------------------------------------------------------------------------- #

# Basic patterns – they ignore comments, string literals etc.
CLASS_RE = re.compile(
    r"""(?P<indent>\s*)                # indentation
        (public\s+)?                  # optional public
        (final\s+)?                   # optional final
        class\s+
        (?P<name>\w+)\b
    """,
    re.VERBOSE,
)

# # Capture return type + method name + params.
# METHOD_RE = re.compile(
#     r"""(?P<indent>\s*)                # indentation
#         (public|private|protected|static|\s)+\s+
#         (?P<ret_type>[\w<>\[\]]+)\s+   # REQUIRED return type + space
#         (?P<name>\w+)\s*
#         \((?P<params>[^)]*)\)\s* # parameters
#         (throws\s+[^{]+)?\s*
#         \{?                            # possible opening brace
#     """,
#     re.VERBOSE,
# )

# Updated METHOD_RE to require explicit method modifiers and catch constructors.
METHOD_RE = re.compile(
    r"""(?P<indent>\s*)                    # 1. indentation
        # REQUIRE one or more method/constructor modifiers (public, private, static, etc.)
        (?:\b(?:public|private|protected|static|final|abstract|synchronized|native|strictfp)\b\s+)+
        # Optional space and other modifiers that might be mixed in
        (?:\s*\b(?:static|final|abstract|synchronized|native|strictfp)\b)*\s*
        (?:                                    # 2. Non-capturing group for return type (optional)
            (?P<ret_type>[\w<>\[\].]+)\s+      # 3. Return type
        )?                                     # 4. Make return type group optional
        (?P<name>\w+)\s* # 5. Method/Constructor name
        \((?P<params>[^)]*)\)\s* # 6. parameters
        (?:throws\s+[^{]+)?\s*
        # 7. Match either an opening brace or a semicolon (covers concrete, abstract, and interface methods)
        [;\{]? 
    """,
    re.VERBOSE,
)

# Javadoc block pattern (non-greedy)
JAVADOC_RE = re.compile(
    r"""/\*\*
       (?:\s*\*.*?)*?                # lines starting with *
       \*/\s*
    """,
    re.DOTALL | re.MULTILINE,
)

# Extract tags from a Javadoc block
TAG_RE = re.compile(r"@(\w+)(?:\s+([^\n@]*))?")

# --------------------------------------------------------------------------- #
#                            Helper data structures
# --------------------------------------------------------------------------- #

@dataclass
class ClassInfo:
    name: str
    start: int          # line number of the class declaration
    end: int            # line number of the closing brace
    javadoc_start: Optional[int]  # line number of Javadoc block start
    javadoc_end: Optional[int]    # line number of Javadoc block end
    methods: List["MethodInfo"]

@dataclass
class MethodInfo:
    name: str
    params: List[Tuple[str, str]]  # (type, name)
    return_type: str
    start: int
    end: int
    javadoc_start: Optional[int]
    javadoc_end: Optional[int]


# --------------------------------------------------------------------------- #
#                            Parsing utilities
# --------------------------------------------------------------------------- #

def parse_file(lines: List[str]) -> List[ClassInfo]:
    """
    Parse a list of lines from a Java file and return a list of ClassInfo
    objects with nested MethodInfo objects.
    """
    classes: List[ClassInfo] = []
    stack = []  # (indent, 'class'/'method', start_line, name)

    # Pre‑compute Javadoc positions
    javadoc_positions = {}
    for m in JAVADOC_RE.finditer("".join(lines)):
        start = m.start()
        end = m.end()
        # Find line number (simplistic conversion)
        line_start = sum(len(l) + 1 for l in lines[:m.start()]) 
        javadoc_positions[start] = (start, end)

    # We'll scan line by line, looking for class/method declarations.
    for idx, line in enumerate(lines):
        # Check for class
        cls_match = CLASS_RE.search(line)
        if cls_match:
            name = cls_match.group("name")
            indent = cls_match.group("indent")
            
            # Javadoc search logic (using character offsets)
            javadoc_start = None
            javadoc_end = None
            line_offset = sum(len(l) + 1 for l in lines[:idx])
            
            for j_start, (s, e) in javadoc_positions.items():
                if e == line_offset + cls_match.start():  # contiguous check
                    javadoc_start = s
                    javadoc_end = e
                    break
            
            # Create a placeholder for end line – we will compute it later
            cls_info = ClassInfo(
                name=name,
                start=idx,
                end=None,
                javadoc_start=javadoc_start,
                javadoc_end=javadoc_end,
                methods=[],
            )
            stack.append(("class", cls_info))
            continue

        # Check for method
        meth_match = METHOD_RE.search(line)
        if meth_match:
            name = meth_match.group("name")
            
            # --- MODIFIED LOGIC FOR CONSTRUCTORS/RETURN TYPE ---
            ret_type_match = meth_match.group("ret_type")
            
            if ret_type_match:
                ret_type = ret_type_match
            else:
                # No explicit return type matched: check if it's a constructor
                current_class_name = None
                for c in reversed(stack):
                    if c[0] == "class":
                        current_class_name = c[1].name
                        break
                        
                if current_class_name and name == current_class_name:
                    # Constructor: its return "type" is the class name
                    ret_type = current_class_name
                else:
                    # Assume 'void' if no return type is explicitly found
                    ret_type = "void" 
            # ----------------------------------------------------

            params_str = meth_match.group("params").strip()
            params = []
            if params_str:
                for p in params_str.split(","):
                    p = p.strip()
                    if not p:
                        continue
                    # Split by last space to handle types with spaces
                    *type_parts, param_name = p.split()
                    p_type = " ".join(type_parts)
                    params.append((p_type, param_name))
            
            # Javadoc search logic (using character offsets)
            javadoc_start = None
            javadoc_end = None
            line_offset = sum(len(l) + 1 for l in lines[:idx])
            
            for j_start, (s, e) in javadoc_positions.items():
                if e == line_offset + meth_match.start():
                    javadoc_start = s
                    javadoc_end = e
                    break
            
            meth_info = MethodInfo(
                name=name,
                params=params,
                return_type=ret_type,
                start=idx,
                end=None,
                javadoc_start=javadoc_start,
                javadoc_end=javadoc_end,
            )
            stack.append(("method", meth_info))
            continue

        # Detect closing braces to determine end lines
        if "}" in line:
            if stack:
                typ, obj = stack.pop()
                obj.end = idx
                if typ == "method":
                    # Add to current class
                    for c in reversed(stack):
                        if c[0] == "class":
                            c[1].methods.append(obj)
                            break
                elif typ == "class":
                    classes.append(obj)

    return classes


# --------------------------------------------------------------------------- #
#                          Javadoc creation / update
# --------------------------------------------------------------------------- #

def javadoc_exists(javadoc_start: Optional[int], javadoc_end: Optional[int]) -> bool:
    return javadoc_start is not None and javadoc_end is not None


def extract_javadoc_tags(lines: List[str], start: int, end: int) -> dict:
    """
    Given the line numbers of a Javadoc block, return a dict of tag -> text
    """
    text = "\n".join(lines[start:end])
    tags = {}
    for tag_match in TAG_RE.finditer(text):
        tag = tag_match.group(1)
        value = tag_match.group(2)
        tags[tag] = value.strip() if value else ""
    return tags


def create_class_javadoc(class_name: str, summary: str) -> List[str]:
    """
    Return a list of lines that form a Javadoc block for a class,
    using the LLM-generated summary.
    """
    lines = [
        "/**",
        " * @author kata_du",
        " * @version 1.0.0",
        f" * @date {datetime.date.today():%Y-%m-%d}",
        f" *@summary {summary}", # Use the LLM-generated summary here
        " */",
    ]
    return lines


def create_method_javadoc(
    method: MethodInfo,
    description: str,
    param_descs: dict,
    return_desc: str,
) -> List[str]:
    """
    Build a Javadoc block for a method using LLM-generated description and tags.
    """
    lines = ["/**"]
    
    # 1. Add the main description/summary from the LLM
    wrapped_description = textwrap.wrap(description, width=70)
    for line in wrapped_description:
        lines.append(f" * {line}")
        
    lines.append(" *")

    # 2. Add @param tags
    # --- FIX 1: Corrected iteration order to (type, name) ---
    for p_type, p_name in method.params:
        
        # --- FIX 2: Replaced the recursive 'desc' with a safe string literal fallback ---
        desc = param_descs.get(p_name, "Parameter description") 
        
        # Note: Standard Javadoc only uses @param [name] [description]
        lines.append(f" * @param {p_name} {desc}") 

    # 3. Add @return tag (Only added if return type is not void)
    if method.return_type != "void":
        lines.append(f" * @return {return_desc}")
    
    lines.append(" */")
    return lines
# --------------------------------------------------------------------------- #
#                          Main processing logic
# --------------------------------------------------------------------------- #
def process_file(file_path: Path) -> None:
    print(f"Scanning {file_path}")
    lines = file_path.read_text(encoding="utf-8").splitlines()
    classes = parse_file(lines)
    
    # We will build a new list of lines
    new_lines = lines.copy()
    offset = 0  # line number shift due to insertions
    
    for cls in classes:
        # --- 1. Class Javadoc Regeneration ---

        # 1a. Prepare LLM prompt for Class Summary:
        class_details = f"Class: {cls.name}\n"
        class_details += "Methods:\n"
        for meth in cls.methods:
            param_list = ", ".join(f"{p_type} {p_name}" for p_type, p_name in meth.params)
            class_details += f"- {meth.return_type} {meth.name}({param_list})\n"
        
        prompt_class_summary = (
            f"Provide a concise summary for the Java class `{cls.name}` based on its structure and methods. "
            f"The summary should be a single sentence describing its primary responsibility. "
            f"Details: {class_details}"
        )
        
        # DEBUG LOG: Class Summary Prompt
        print(f"\n[DEBUG LLM] Sending prompt for Class Summary ({cls.name}):\n{textwrap.indent(prompt_class_summary, '>>> ')}")
        
        # Always ask LLM for summary
        class_summary = llm_call(prompt_class_summary,system_prompt=sys_prompt_java_commentor,service='b') 
        
        # DEBUG LOG: Class Summary Response
        print(f"[DEBUG LLM] Received response for Class Summary:\n<<< {class_summary}")

        # 1b. Build the new class Javadoc content
        javadoc_lines = create_class_javadoc(
            cls.name, summary=class_summary
        )

        # 1c. DELETE existing Javadoc and INSERT new one
        if javadoc_exists(cls.javadoc_start, cls.javadoc_end):
            # 1. DELETE existing Javadoc block
            start_line = cls.javadoc_start + offset
            end_line = cls.javadoc_end + offset
            lines_deleted = end_line - start_line
            
            new_lines[start_line:end_line] = []
            
            # Adjust offset based on deletion
            offset -= lines_deleted
            insert_at = start_line # New Javadoc inserts at the cleared position
        else:
            # No existing Javadoc, insert before class declaration
            insert_at = cls.start + offset
        
        # 2. INSERT new Javadoc block
        new_lines[insert_at:insert_at] = javadoc_lines
        
        # Adjust offset based on insertion
        offset += len(javadoc_lines)
            
        print(f"  Updated class Javadoc for {cls.name}")

        # --- 2. Process Methods Javadoc Regeneration ---
        for meth in cls.methods:
            # 2a. Prepare Method Body
            method_code = "\n".join(lines[meth.start : meth.end + 1]) # Assuming end is inclusive

            # 2b. Always ask LLM for Method Summary/Description
            prompt_method_description = (
                f"Provide a concise summary for the Java method `{meth.name}`. "
                f"The summary should be a single sentence describing its primary responsibility. "
                f"\n\nMethod Code:\n{method_code}"
            )
            
            # DEBUG LOG: Method Description Prompt
            print(f"\n[DEBUG LLM] Sending prompt for Method Description ({cls.name}.{meth.name}):\n{textwrap.indent(prompt_method_description, '>>> ')}")
            
            method_description = llm_call(prompt_method_description,system_prompt=sys_prompt_java_commentor,service='b') # Always ask LLM
            
            # DEBUG LOG: Method Description Response
            print(f"[DEBUG LLM] Received response for Method Description:\n<<< {method_description}")

            # 2c. Always ask LLM for Parameter Descriptions (One call per parameter)
            param_descs = {}
            if meth.params:
                param_list_str = ", ".join(f"{p_type} {p_name}" for p_type, p_name in meth.params)
                
                # --- Robust Loop for individual parameter description calls ---
                for p_type, p_name in meth.params:
                    
                    prompt_param_desc = (
                        f"Provide a concise, single-sentence description for the Java parameter `{p_name}` "
                        f"of type `{p_type}` in method `{meth.name}`. "
                        f"Method signature: {meth.return_type} {meth.name}({param_list_str})."
                    )

                    # DEBUG LOG: Individual Parameter Description Prompt
                    print(f"\n[DEBUG LLM] Sending prompt for Parameter Description ({cls.name}.{meth.name} / {p_name}):\n{textwrap.indent(prompt_param_desc, '>>> ')}")

                    try:
                        llm_response = llm_call(prompt_param_desc,system_prompt=sys_prompt_java_commentor,service='b'
                                                # ,temperature=0.1, max_tokens=60
                                                )
                        
                        # Assignment to dictionary ONLY on success
                        param_descs[p_name] = llm_response
                        
                        # DEBUG LOG: Individual Parameter Description Response
                        print(f"[DEBUG LLM] Received response for Parameter Description ({p_name}):\n<<< {llm_response}")
                        
                    except Exception as e:
                        # Fallback assignment to dictionary on failure
                        print(f"[ERROR] Failed to get LLM response for parameter {p_name}: {e}")
                        param_descs[p_name] = "No description provided."
            
            # 2d. Always ask LLM for Return Description (UNCONDITIONALLY ASKED)
            return_desc = ""
            
            prompt_return = (
                f"Provide a concise description of the return value for the Java method "
                f"`{meth.name}` which returns a `{meth.return_type}`. Base the description on "
                f"what the method's purpose is. Return value type is {meth.return_type}."
            )

            # DEBUG LOG: Return Description Prompt
            print(f"\n[DEBUG LLM] Sending prompt for Return Description ({cls.name}.{meth.name}):\n{textwrap.indent(prompt_return, '>>> ')}")

            return_desc = llm_call(prompt_return, system_prompt=sys_prompt_java_commentor,service='b'
                                #    ,temperature=0.1, max_tokens=60
                                   )
            
            # DEBUG LOG: Return Description Response
            print(f"[DEBUG LLM] Received response for Return Description:\n<<< {return_desc}")
            
            # 2e. Build new method Javadoc
            javadoc_lines = create_method_javadoc(
                meth, method_description, param_descs, return_desc
            )
            
            # 2f. DELETE existing Javadoc and INSERT new one
            if javadoc_exists(meth.javadoc_start, meth.javadoc_end):
                # 1. DELETE existing Javadoc block
                start_line = meth.javadoc_start + offset
                end_line = meth.javadoc_end + offset
                lines_deleted = end_line - start_line
                
                new_lines[start_line:end_line] = []
                
                # Adjust offset based on deletion
                offset -= lines_deleted
                insert_at = start_line # New Javadoc inserts at the cleared position
            else:
                # No existing Javadoc, insert before method declaration
                insert_at = meth.start + offset
                
            # 2. INSERT new Javadoc block
            new_lines[insert_at:insert_at] = javadoc_lines
            
            # Adjust offset based on insertion
            offset += len(javadoc_lines)
            
            print(f"  Updated method Javadoc for {meth.name}")
            
    # Write back if changes were made
    if new_lines != lines:
        file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        print(f"  → File updated: {file_path}")
    else:
        print("  → No changes needed.")

def walk_and_process(root: Path) -> None:
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if fname.endswith(".java"):
                file_path = Path(dirpath) / fname
                try:
                    process_file(file_path)
                except Exception as exc:
                    print(f"ERROR processing {file_path}: {exc}", file=sys.stderr)


# --------------------------------------------------------------------------- #
#                              CLI
# --------------------------------------------------------------------------- #

def main() -> None:
    root_path = r"C:\Users\kata_du\git\cameo_git_test\GitLab_syncronization\src\gitplugin"

    print(f"Starting scan at {root_path}\n")
    walk_and_process(root_path)
    print("\nAll done.")


if __name__ == "__main__":
    main()