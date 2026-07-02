
Sys_prompt_cla= """
You are an expert research assistant highly specialized in aerospace engineering, 
specifically in Systems Engineering, Safety Engineering, Model-Based Systems Engineering (MBSE), 
Model-Based Safety Assessment (MBSA), and Aircraft System Certification. 

You will be given:
1. A publication title from these domains.
2. A single yes/no question that tests whether the title matches a specific classification pattern 
   (e.g., STUDY_ILLUSTRATION, METHOD_EXPLAINING, etc.).

Your task:
- Analyze only the exact title text — do not assume or infer missing context.
- Focus solely on the linguistic and structural cues in the title (e.g., keywords, phrasing, category indicators).
- Evaluate carefully whether the title clearly satisfies the condition described by the question. 
- Be strict: respond "True" only when the title provides clear evidence from wording or structure; otherwise, respond "False".
- Do not guess based on general knowledge or likely meaning.

Output format:
Reply with only one word — exactly either “True” or “False”.
Do not include explanations, punctuation, or extra text.
"""

Prompt_TEMP_ClAS="""
You will now evaluate one title and one classification question.

Title:
"{Title}"

Question:
"{Question}"

Please determine if the title clearly satisfies the condition stated in the question, 
following the evaluation rules from the system instructions. 
Respond with exactly one word — "True" or "False".

"""

# ============================================================================
# CLASSIFICATION CONFIGURATION (FIELD-AGNOSTIC)
# ============================================================================

FIELD_AGNOSTIC_CLASSIFICATION_SECTIONS = {
    "STUDY_ILLUSTRATION": [
        "Does the title explicitly include the term 'case study' or 'case-based'?",
        "Does the title use the word 'application' or 'applied' (e.g., 'Application of X to Y')?",
        "Does the title mention 'evaluation', 'validation', or 'assessment' of a tool or method in a specific setting?",
        "Does the title describe an 'experiment', 'experimental study', or 'empirical study'?",
        "Does the title mention a 'field study', 'field test', or 'real-world' trial?",
        "Does the title focus on the 'implementation' or 'deployment' of a solution in a concrete scenario?",
        "Does the title mention 'lessons learned' or 'insights from' a specific project or experience?",
        "Does the title describe a 'pilot study' or 'feasibility study'?",
        "Does the title specify a particular industry, organization, or dataset where a method was tested?",
        "Does the title indicate a 'demonstration' or 'illustration' of a concept using a specific example?",
        "Does the title suggest the paper is reporting on 'results' from a specific intervention or trial?"
    ],

    "METHOD_EXPLAINING": [
        "Does the title start with or contain phrases like 'A method for...', 'An approach to...', or 'A technique for...'?",
        "Does the title explicitly introduce a 'new algorithm', 'novel algorithm', or 'improved algorithm'?",
        "Does the title use verbs like 'propose', 'present', 'introduce', or 'develop' in relation to a solution or model?",
        "Does the title describe the design or formulation of a 'model', 'system', or 'mechanism'?",
        "Does the title emphasize a 'solution' to a specific technical problem?",
        "Does the title mention a specific 'technique' or 'scheme' (e.g., 'A clustering technique for...')?",
        "Does the title describe a 'hybrid approach' or 'integrated method'?",
        "Does the title focus on 'optimizing' or 'enhancing' an existing method?",
        "Does the title name a specific tool or solver developed by the authors?",
        "Does the title mention 'construction', 'synthesis', or 'derivation' of a methodology?",
        "Does the title promise a 'robust' or 'efficient' way to perform a task?"
    ],

    "PROCESS_TO_BE_FOLLOWED": [
        "Does the title explicitly use the words 'process', 'workflow', 'procedure', or 'protocol'?",
        "Does the title mention 'guidelines', 'guiding principles', or 'best practices'?",
        "Does the title describe a 'methodology' (implying a structured set of methods) rather than a single algorithm?",
        "Does the title mention a 'roadmap' or 'pathway' for achieving a goal?",
        "Does the title suggest a 'step-by-step' guide or 'how-to' approach?",
        "Does the title describe a 'lifecycle' or 'development cycle'?",
        "Does the title mention a 'standard operating procedure' or 'compliance framework'?",
        "Does the title focus on 'governance', 'management', or 'administration' steps?",
        "Does the title propose a 'checklist' or 'audit' method?",
        "Does the title describe a 'strategy' for implementation or adoption?",
        "Does the title mention 'protocols' for standardization or reproducibility?"
    ],

    "SYSTEMATIC_LITERATURE_REVIEW": [
        "Does the title explicitly contain the phrase 'systematic literature review' or 'systematic review'?",
        "Does the title mention 'systematic mapping' or 'mapping study'?",
        "Does the title include the term 'meta-analysis'?",
        "Does the title refer to a 'bibliometric analysis' or 'scientometric analysis'?",
        "Does the title mention using 'PRISMA' or a similar formal review protocol?",
        "Does the title describe a 'scoping review'?",
        "Does the title promise a 'comprehensive systematic' survey of the field?",
        "Does the title mention 'structured review' or 'evidence synthesis'?",
        "Does the title focus on 'aggregating' or 'pooling' data from previous studies?",
        "Does the title explicitly state it reviews literature using a 'defined search strategy'?",
        "Does the title refer to a 'quantitative review' of existing research?"
    ],

    "LITERATURE_REVIEW": [
        "Does the title contain the words 'review', 'survey', or 'overview' *without* the word 'systematic'?",
        "Does the title start with 'A Review of...' or 'A Survey of...'?",
        "Does the title describe the paper as a 'tutorial' or 'primer'?",
        "Does the title mention 'background', 'history', or 'evolution' of a topic?",
        "Does the title frame itself as an 'introductory' text or 'guide' to a field?",
        "Does the title promise a 'summary' or 'synopsis' of recent work?",
        "Does the title mention 'revisiting' or 're-examining' a classic concept?",
        "Does the title describe a 'critical review' or 'narrative review'?",
        "Does the title focus on 'concepts and definitions' in a broad sense?",
        "Does the title claim to 'synthesize' literature in a non-statistical way?",
        "Does the title discuss the 'status' of a field in general terms?"
    ],

    "STATE_OF_THE_ART_ANALYSIS": [
        "Does the title explicitly use the phrase 'state of the art' (SOTA)?",
        "Does the title mention 'current trends', 'emerging trends', or 'recent advances'?",
        "Does the title promise a 'comparative analysis' or 'comparison' of multiple existing methods?",
        "Does the title discuss 'challenges and opportunities' or 'open problems'?",
        "Does the title mention 'future directions', 'perspectives', or 'vision' for the field?",
        "Does the title describe the 'landscape' or 'horizon' of a technology or field?",
        "Does the title assess the 'maturity' or 'readiness' of different approaches?",
        "Does the title mention 'gaps' in the current research?",
        "Does the title focus on 'benchmarking' multiple leading tools against each other?",
        "Does the title discuss 'frontiers' or 'cutting-edge' developments?",
        "Does the title analyze the 'status quo' versus 'future needs'?"
    ],

    "OTHER_SIMILAR_CONCEPTS": [
        "Does the title explicitly mention a 'framework' (e.g., 'Conceptual Framework', 'Theoretical Framework')?",
        "Does the title describe a 'taxonomy', 'typology', or 'classification scheme'?",
        "Does the title introduce an 'ontology' or 'meta-model'?",
        "Does the title mention an 'architecture' or 'reference model' (conceptual structure)?",
        "Does the title discuss a 'paradigm' or 'paradigm shift'?",
        "Does the title focus on 'theoretical foundations' or 'formal definitions'?",
        "Does the title present a 'position paper' or 'opinion piece'?",
        "Does the title describe a 'unifying perspective' or 'holistic view'?",
        "Does the title discuss 'terminology' or 'standardization' of concepts?",
        "Does the title propose a 'conceptual model' without a specific implementation?",
        "Does the title focus on 'abstraction' or 'generalization' of ideas?"
    ]
}


# Classification sections with their respective questions
CLASSIFICATION_SECTIONS = {
    "STUDY_ILLUSTRATION": [
        "Does the title explicitly include terms such as 'case study', 'industrial case', 'case-based', or 'field study' in the context of aircraft or avionics systems?",
        "Does the title use the word 'application' or 'applied' (e.g., 'Application of MBSE to … in Aircraft System Development')?",
        "Does the title mention 'evaluation', 'assessment', 'validation', or 'verification' of a method, model, or process on a specific aircraft system or function?",
        "Does the title specify a concrete aircraft program, platform, or subsystem (e.g., 'for a regional jet', 'for an unmanned aerial vehicle')?",
        "Does the title include 'experimental study', 'simulation study', 'pilot study', or 'feasibility study' in an aerospace context?",
        "Does the title describe 'implementation' or 'deployment' of a systems/safety/MBSE/MBSA method in an aircraft development project?",
        "Does the title use phrasing like 'experience from', 'lessons learned from', or 'insights from' a specific aircraft development or certification project?",
        "Does the title pair a technique with a concrete domain, such as 'Fault Tree Analysis for the Hydraulic System of a Civil Aircraft'?",
        "Does the title highlight 'benchmarking' or 'performance comparison' of methods on a particular aircraft-related use case or dataset?",
        "Does the title contain 'in practice', 'industrial practice', or 'real-world application' referring to aviation or aircraft system development?",
        "Does the title explicitly say 'applied to aircraft systems', 'in the development of [aircraft X]', or 'for an aircraft safety case'?"
    ],
    
    "METHOD_EXPLAINING": [
        "Does the title start with or contain 'A method for…', 'A model-based approach to…', or 'An approach for…' in the context of aircraft system or safety engineering?",
        "Does the title include 'method', 'approach', 'technique', 'algorithm', or 'formalism' without explicit mention of a specific case study?",
        "Does the title use verbs like 'propose', 'introduce', 'develop', 'design', 'define', or 'present' for a new or refined method targeting aircraft systems or certification?",
        "Does the title describe a specific modeling or analysis technique such as 'contract-based design', 'fault propagation modeling', or 'formal verification method'?",
        "Does the title emphasize 'model-based' or 'MBSE/MBSA' as the primary contribution?",
        "Does the title refer to an 'integrated method', 'combined approach', or 'co-engineering method' for systems and safety in aircraft development?",
        "Does the title highlight 'automatic' or 'semi-automatic' analysis or generation (e.g., 'Automatic Generation of Safety Artifacts from MBSE Models')?",
        "Does the title name a specific methodological concept (e.g., 'pattern-based safety analysis', 'constraint-based configuration')?",
        "Does the title suggest a generic method not limited to one case, using phrases like 'for complex aircraft systems' or 'for safety-critical avionics'?",
        "Does the title emphasize 'how to model', 'how to analyze', or 'how to integrate' safety, systems, or certification aspects in a method-like way?",
        "Does the title introduce a method name or acronym (e.g., 'The AIRMETH Method for Model-Based Safety Assessment')"
    ],
    
    "PROCESS_TO_BE_FOLLOWED": [
        "Does the title explicitly contain the words 'process', 'workflow', 'procedure', 'methodology', or 'lifecycle' in the context of aircraft system development?",
        "Does the title refer to 'development process' or 'engineering process' for aircraft or avionics systems?",
        "Does the title describe a process 'for integrating safety into systems engineering' or 'for applying MBSE' in aircraft development?",
        "Does the title mention 'guidelines', 'best practices', or 'practice guidelines' for safety, MBSE, MBSA, or certification activities?",
        "Does the title refer to 'process improvement', 'process framework', or 'reference process' for aircraft systems or certification?",
        "Does the title include 'framework for conducting', 'stepwise approach to', or 'phased approach to' aircraft system or safety activities?",
        "Does the title explicitly connect to standards like 'DO‑178C process', 'DO‑254 process', 'ARP4754A process', or 'ARP4761 process'?",
        "Does the title talk about an 'end-to-end process', 'integrated development process', or 'coordinated process' across systems and safety engineering?",
        "Does the title emphasize 'procedures' or 'protocols' for certification, compliance demonstration, or safety assessment of aircraft systems?",
        "Does the title describe a 'roadmap', 'step-by-step approach', or 'implementation strategy' for adopting MBSE or MBSA in aircraft projects?",
        "Does the title highlight 'process alignment', 'process harmonization', or 'process tailoring' for aircraft system development and certification?"
    ],
    
    "SYSTEMATIC_LITERATURE_REVIEW": [
        "Does the title explicitly use the phrase 'Systematic Literature Review' in the context of systems/safety/MBSE/MBSA/certification for aircraft?",
        "Does the title contain 'Systematic Review', 'Systematic Mapping Study', or 'Systematic Mapping' related to aircraft systems engineering or safety?",
        "Does the title indicate a 'systematic survey' or 'systematic overview' of methods, tools, or practices in aircraft system development?",
        "Does the title mention 'meta-analysis' or 'meta-study' of approaches in MBSE, MBSA, or safety engineering for aerospace systems?",
        "Does the title refer to a 'PRISMA-based review', 'structured review', or 'protocol-based review' in this domain?",
        "Does the title say 'A Systematic Review of … for Aircraft Systems' or similar wording?",
        "Does the title promise a 'comprehensive and systematic analysis' of existing studies on, for example, model-based safety assessment in aviation?",
        "Does the title use 'systematic' together with 'mapping', 'scoping', or 'review' of research on aircraft system development or certification?",
        "Does the title emphasize 'classification and analysis of existing approaches' as part of a systematic survey of the literature?",
        "Does the title explicitly focus on 'published research' or 'academic studies' using systematic review terminology in the aerospace context?",
        "Does the title include both a topic (e.g., 'MBSE for Aircraft Systems') and the label 'systematic literature review' or similar formal review term?"
    ],
    
    "LITERATURE_REVIEW": [
        "Does the title contain words like 'Literature Review', 'Survey', 'Overview', or 'Critical Review' without the adjective 'systematic'?",
        "Does the title say 'A Review of…', 'Overview of…', or 'Survey of…' methods, tools, or practices in aircraft systems engineering or safety?",
        "Does the title refer to a 'review of MBSE approaches for aircraft system development' or similar?",
        "Does the title present a 'survey of model-based safety assessment techniques' in aviation or aerospace?",
        "Does the title use 'state-of-practice review', 'technology review', or 'tool review' for MBSE, MBSA, or certification support in aircraft development?",
        "Does the title indicate 'review of standards and guidelines' for aircraft system safety or certification (e.g., ARP4754A, ARP4761, DO‑178C)?",
        "Does the title say 'overview of safety analysis methods' or 'overview of systems engineering practices' in aircraft development?",
        "Does the title describe a 'narrative review', 'tutorial review', or 'introductory review' in these technical areas?",
        "Does the title focus on giving a broad picture or background of a topic, e.g., 'A Review of Digital Twin Concepts in Aircraft Certification'?",
        "Does the title combine 'review' with 'trends', 'challenges', or 'opportunities' without explicitly using 'state-of-the-art' phrasing?",
        "Does the title signal an educational or background intention, such as 'An Introductory Survey of MBSE for Aircraft Systems'?"
    ],
    
    "STATE_OF_THE_ART_ANALYSIS": [
        "Does the title explicitly use the phrase 'State of the Art' in connection with systems engineering, safety engineering, MBSE, MBSA, or certification for aircraft?",
        "Does the title mention 'Current Practices', 'Current Approaches', 'Current Methods', or 'Existing Techniques' in the aircraft domain?",
        "Does the title refer to 'Recent Advances', 'Recent Developments', or 'Advances in' aircraft systems or safety engineering methods?",
        "Does the title talk about 'Trends', 'Emerging Trends', or 'Future Directions' in MBSE/MBSA or aircraft system certification?",
        "Does the title describe a 'comparative analysis' or 'comparative study' of multiple existing methods or tools used in aircraft system development?",
        "Does the title highlight 'challenges and opportunities', 'gaps and perspectives', or 'limitations of current practice' in this domain?",
        "Does the title discuss 'maturity of methods', 'technology readiness', or 'industrial adoption' of systems/safety/MBSE methods for aircraft systems?",
        "Does the title include 'landscape of…', 'panorama of…', or 'status of…' techniques or tools in aircraft systems engineering or safety analysis?",
        "Does the title emphasize 'frontiers', 'cutting-edge', 'next-generation', or 'future-proof' approaches for aircraft system development or certification?",
        "Does the title frame itself as a 'state-of-practice assessment' or 'industry status report' on safety or systems engineering methods in aviation?",
        "Does the title suggest an evaluative positioning of existing methods?"
    ],
    
    "OTHER_SIMILAR_CONCEPTS": [
        "Does the title prominently feature the words 'framework', 'conceptual framework', or 'methodological framework' for aircraft systems or safety engineering?",
        "Does the title describe an 'architecture', 'reference architecture', or 'reference model' for MBSE/MBSA or safety-critical aircraft systems?",
        "Does the title refer to a 'conceptual model', 'meta-model', or 'domain model' for representing aircraft systems or safety information?",
        "Does the title introduce a 'taxonomy', 'classification scheme', or 'ontology' of methods, hazards, models, or artifacts in aircraft system development?",
        "Does the title mention a 'paradigm', 'concept', 'vision', or 'conceptual approach' to systems, safety, or certification in aviation?",
        "Does the title use 'integrated concept', 'holistic concept', or 'unifying concept' for combining systems engineering, safety, and certification aspects?",
        "Does the title present a 'reference framework for integrating MBSE and safety analysis' without strongly emphasizing process steps?",
        "Does the title focus on 'relationships between artifacts', 'traceability concepts', or 'information architecture' in aircraft system development?",
        "Does the title describe a 'theoretical perspective', 'theoretical foundation', or 'formal framework' for safety or systems engineering of aircraft?",
        "Does the title introduce a 'modeling language profile', 'extension', or 'notation' (e.g., an extension of SysML for safety)?",
        "Does the title talk about a 'framework for aligning' or 'mapping between' standards (e.g., ARP4754A and ARP4761) in a conceptual way?"
    ]
}

