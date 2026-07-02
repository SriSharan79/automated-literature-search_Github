
import pandas as pd

def extract_column(file_path: str, column_name: str) -> list:
    """
    Extracts all data from a specified column in a single-sheet Excel file into a list.
    It automatically reads the first sheet.

    Args:
        file_path (str): The full path to the Excel file (e.g., 'C:/data/input.xlsx').
        column_name (str): The name of the column to extract.

    Returns:
        List: A list containing all the values from the specified column.
    """
    try:
        # Read the entire Excel file (it defaults to the first sheet)
        # Setting header=0 (the default) ensures it uses the first row as column names
        df = pd.read_excel(file_path)

        # Check if the column exists
        if column_name in df.columns:
            # Extract the column data and convert it to a Python list
            data_list = df[column_name].tolist()
            return data_list
        else:
            print(f"Error: Column '{column_name}' not found in the sheet.")
            return []

    except FileNotFoundError:
        print(f"Error: File not found at path: {file_path}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return []

Keywords_literature_process = [
    "Research Gaps",
    "Literature Review",
    "Gap Analysis",
    "Knowledge Gaps",
    "Systematic Review",
    "Meta-analysis",
    "State of the Art",
    "Research Trends",
    # "Emerging Topics",
    # "Research Challenges",
    # "Unexplored Areas",
    "Research Opportunities"
    "Innovation Gaps",
    "Current Literature",
    "Previous Studies",
    "Research Limitations",
    "Theoretical Gaps",
    "Methodological Gaps",
    "Practical Implications",
    "Technology Advancements",
    "Critical Review",
    "Research Directions",
    "Hypothesis Development",
    "Future Research",
    "Research Objectives",
    "Comparative Analysis",
    "Field Knowledge",
    "Sector-Specific Gaps",
    "Interdisciplinary Research",
    "Literature Mapping",
    "Research Synthesis",
    "Research Implications",
    "Innovative Approaches",
    "Unresolved Issues",
    "Study Limitations",
    "Research Scope",
    "Evidence Gaps",
    "Research Methods Comparison",
    "Synthesis of Findings",
    "Research Landscape"
]

keyphrases_literature_review = [
    # Literature Review (General)
    "literature review methodology",
    "steps for writing a literature review",
    "literature review protocol",
    "scoping review methodology",
    "meta-analysis procedure",
    "developing a search string",
    "PICO framework",
    "thematic synthesis",
    "literature synthesis techniques",
    # Systematic Literature Review (SLR) 🌟
    "systematic literature review **guidelines**",
    "systematic review **search strategy**",
    "PRISMA **statement**",
    "conducting a systematic review",
    "systematic review inclusion exclusion criteria",
    "systematic review quality assessment",
    "systematic review **data extraction** form",
    "critical appraisal systematic review",
    "systematic review reporting standards",
    "protocol systematic review registration",
    # State-of-the-Art Analysis
    "state-of-the-art analysis methodology",
    "conducting a state-of-the-art review",
    "defining state of the art",
    "identifying research gaps",
    "current trends and challenges",
    "future research directions",
    "cutting-edge research review",
    "recent advances literature review",
    "research landscape analysis",
    # Systematic Mapping Study
    "systematic mapping study protocol",
    "conducting a systematic map",
    "systematic mapping guidelines",
    "systematic mapping study steps",
    "systematic map data extraction",
    "systematic map visualization techniques",
    "visualizing research gaps",
    "Petersen systematic mapping",
    "Kitchenham systematic review guidelines"
]


AI_KEYWORDS=[
'Machine Learning (ML)',
'Deep Learning (DL)', 
'Generative AI', 
'Large Language Models (LLMs)',
'Neural Networks', 
'Reinforcement Learning (RL)', 
'Natural Language Processing (NLP)',
'Explainable AI (XAI)',
# 'Algorithmic Bias', 
# 'Foundation Models',
'AI Agents'
]

REQ_KEYWORDS= [ 
"requirements engineering",
"requirements elicitation",
"requirements analysis",
"requirements modeling",
"requirements specification",
"requirements validation",
"requirements management"
]

DOMAIN_KEYWORDS=[
    'System Engineering',
    'Safety engineering',
    'Model-Based Systems Engineering (MBSE)',
    'Model-Based Safety Analysis (MBSA)',
    'SysML (System Modeling Language)',
    'Risk Assessment/Mitigation',
    'Verification and Validation (V&V)',
    "requirements engineering"
]

mbse_activities = [
    "Stakeholder Needs Elicitation and Capture",
    "System Context and Environment Modeling",
    "Use Case Development and Behavioral Analysis",
    "Functional Analysis and Decomposition (Defining system functions)",
    "Logical Architecture Definition (Conceptual structure of the system)",
    "Interface Definition and Specification",
    "Allocation of Requirements and Functions to Architecture Elements",
    "Physical Architecture and Detailed Design (Implementation-level structure)",
    "System Simulation and Performance Analysis",
    "Requirements Traceability and Management",
    "Model-Based Testing and Verification",
    "Model Configuration Management and Version Control",
    "Model-Based Design Reviews and Communication"
]
safety_certification_keywords = [
    "Airworthiness Certification",
    "Type Certification",
    "Airworthiness Authority",
    # "FAA (Federal Aviation Administration)",
    # "EASA (European Union Aviation Safety Agency)",
    # "Certification Specifications (CS)",
    # "CS-25",
    # "Advisory Circular (AC)",
    # "Acceptable Means of Compliance (AMC)",
    # "SAE ARP4754A",
    # "SAE ARP4761",
    # "RTCA DO-178C",
    # "RTCA DO-254",
    # "Development Assurance Level (DAL)",
    # "DAL Level A",
    # "Functional Development Assurance Level (FDAL)",
    # "Item Development Assurance Level (IDAL)",
    "Safety Assessment Process",
    "Safety Requirements",
    "Functional Failure Condition (FFC)",
    "Hazard Severity / Classification",
    # "Catastrophic",
    # "Hazardous",
    # "Major",
    # "Minor",
    # "No Effect",
    # "Probability",
    # "Extremely Remote",
    # "Remote",
    # "Probable",
    # "Safety-Critical",
    # "Safety-Related",
    "Functional Hazard Assessment (FHA)",
    "Preliminary System Safety Assessment (PSSA)",
    "System Safety Assessment (SSA)",
    "Fault Tree Analysis (FTA)",
    "Failure Modes and Effects Analysis (FMEA)",
    "Failure Modes and Effects Summary (FMES)",
    "Common Cause Analysis (CCA)",
    "Common Mode Analysis (CMA)",
    "Zonal Safety Analysis (ZSA)",
    "Particular Risk Analysis (PRA)",
    "Safety Case / Safety Argument",
    "Risk Mitigation",
    "Tolerable Risk",
    # "ALARP (As Low As Reasonably Practicable)",
    # "Life Cycle Data / Artifacts",
    # "Requirements Traceability",
    # "Bidirectional Traceability",
    # "High-Level Requirements (HLR)",
    # "Low-Level Requirements (LLR)",
    # "Verification and Validation (V&V)",
    # "Test Coverage",
    # "Structural Coverage",
    # "MC/DC (Modified Condition/Decision Coverage)",
    # "Independence",
    # "Configuration Management (CM)",
    # "Process Assurance (PA)",
    # "Problem Reports (PR)",
    # "Anomaly Resolution",
    # "Software/Hardware Accomplishment Summary (SAS/HAS)",
    # "Plan for Software/Hardware Aspects of Certification (PSAC/PHAC)",
    # "COTS (Commercial Off-the-Shelf)",
    # "System Architecture",
    # "Integrated Modular Avionics (IMA)",
    # "Line Replaceable Unit (LRU)",
    # "Line Replaceable Module (LRM)",
    # "Single-Fault Criterion",
    "Fail-Safe Design Concept",
    "Continued Airworthiness",
    # "Minimum Equipment List (MEL)"
]

search_phrases_Limtations = [
    # General Limitations of Traditional Methods
    "Limitations of traditional systems engineering in aerospace",
    "Shortcomings of V-model for complex aircraft development",
    "Challenges of document-centric systems engineering in modern aviation",
    "Ineffectiveness of traditional iterative cycles for complex systems",

    # Interdependencies and Dynamic Interactions
    "Complex interdependencies in modern aircraft systems",
    "Managing dynamic interactions in aerospace systems engineering",
    "Emergent behavior in highly integrated aircraft systems",
    "System-of-systems challenges in modern aerospace",

    # Modern Solutions (The Contrast)
    "Model-Based Systems Engineering (MBSE) vs traditional systems engineering for complex aircraft",
    "Transitioning from document-based to model-based aerospace design",
    "MBSE benefits for managing system complexity and integration in aviation",
    "Agile and iterative development in regulated aerospace industry"
]

search_phrases_Data_issues = [
    # Data Transfer and Integration Challenges (Questions)
    "How does seamless data transfer across diverse disciplines in aircraft development fail?",
    "What are the challenges of data exchange across engineering disciplines in aerospace?",
    
    # Data Transfer and Integration Challenges (Keywords)
    "Seamless data transfer in multi-disciplinary aircraft development",
    "Data integration limitations in multi-domain aerospace design",
    "Interoperability issues in aircraft design tools and processes",

    # Discipline-Specific Data Gaps (Keywords)
    "Data handover issues between aerodynamics and structures in aircraft design",
    "Information continuity across electrical, mechanical, and software aircraft teams",
    "Bridging the data gap between CAD, CAE, and manufacturing in aerospace",
    "Siloed data environments in complex aerospace product development",

    # Solutions and Modern Approaches (Keywords)
    "Role of PLM in multi-disciplinary data management in aerospace",
    "Implementing Model-Based Definition (MBD) for seamless data flow in aircraft",
    "Digital Thread and Digital Twin for aircraft data continuity",
    "Standardization efforts for engineering data exchange in aviation"
]

search_phrases_Manual = [
    # Focus on Manual & Labor Dependency
    "Challenges of manual safety assessment in regulated industries",
    "Dependency on human expertise in current regulatory safety review",
    "Labor-intensive processes in safety certification and documentation",
    "Limitations of expert-based safety reviews in aerospace or automotive",

    # Focus on Documentation & Regulatory Aspects
    "Documentation-centric safety compliance challenges",
    "The role of human expertise in regulatory safety documentation review",
    "Digital transformation of regulatory compliance documentation",
    "Drawbacks of current safety assessment documentation and reliance",

    # Focus on Modern Solutions (The Contrast)
    "Automated safety assessment and compliance verification methods",
    "Moving beyond manual safety assessment in certification processes",
    "Impact of digitization on regulatory safety documentation and review",
    "Artificial intelligence (AI) in safety assessment and regulatory compliance"
]

MBSE_MBSA_Phrases=[
    # Model-Based Systems Engineering (MBSE)
    "\"Model-Based Systems Engineering\"",
    "MBSE",
    "\"Model Based System Engineering\"",
    "model* AND system* AND engineer*",
    "MBSE AND aerospace",
    "MBSE AND embedded systems",
    "MBSE AND complex systems",
    "MBSE AND aircraft design",
    "MBSE AND \"digital thread\"",
    "MBSE AND \"digital transformation\"",
    "MBSE AND \"requirements management\"",
    "MBSE AND \"verification and validation\"",

    # Model-Based Safety Assessments (MBSA)
    "\"Model-Based Safety Assessment\"",
    "MBSA",
    "\"Model Based Safety Analysis\"",
    "safety analysis AND model-based",
    "MBSA AND MBSE",
    "MBSA AND system safety",
    "MBSA AND safety certification",
    "MBSA AND \"fault tree analysis\"",
    "MBSA AND \"FHA\"",
    "MBSA AND \"PSSA\"",

    # Aircraft System Development
    "\"aircraft system development\"",
    "\"civil aircraft design\"",
    "\"avionics system design\"",
    "aerospace AND systems engineering",
    "\"aircraft certification process\"",
    "\"system complexity\" AND aircraft safety",
    "\"aerospace data integration\"",
    "\"aircraft systems safety\"",
    "\"aircraft system development\" AND ARP4754B",
    "\"aircraft system development\" AND ARP4761A",

    # System Modeling Language (SysML)
    "SysML",
    "\"Systems Modeling Language\"",
    "SysML AND MBSE",
    "UML AND systems engineering",
    "SysML process chains",
    "SysML AND tool*",
    "SysML AND architecture modeling",
    "SysML AND aerospace",
    "SysML AND \"real-time systems\"",
    "SysML AND \"model transformation\"",

    # Risk Analysis and Assessment Modeling Language (RAAML)
    "RAAML",
    "\"Risk Analysis and Assessment Modeling Language\"",
    "RAAML AND SysML",
    "RAAML AND \"safety modeling\"",
    "RAAML AND MBSA",
    "RAAML AND hazard analysis",
    "safety modeling AND standard*",

    # ARP4754B and ARP4761A
    "ARP4754B",
    "ARP4761A",
    "\"SAE ARP4754B\"",
    "\"SAE ARP4761A\"",
    "ARP4754B AND ARP4761A",
    "ARP4754B AND development assurance",
    "ARP4761A AND safety assessment",
    "\"aircraft safety standards\"",
    "\"DO-178C\" AND ARP4754B",
    "ARP4761A AND FHA",
    "ARP4754B AND DAL assignment",
    "\"system safety assessment\" AND ARP4761A"
]


TOTAL_KEYWORDS=AI_KEYWORDS+REQ_KEYWORDS+DOMAIN_KEYWORDS+mbse_activities+safety_certification_keywords