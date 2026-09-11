# AIthena-Vision: Adaptive Temporal Multimodal Event Retrieval with LLM-generated Multiperspective Fusion

**Tu Van Nguyen¹²³⋆, Nghia Trung Duong¹²³⋆, Nhan Thanh Pham¹²³, Khoi Quoc Le¹²³, and Truong Huu Nguyen Le¹²³**

¹ Faculty of Information Technology, University of Science, Ho Chi Minh City, Vietnam.  
² Vietnam National University, Ho Chi Minh City, Vietnam.  
³ {22127434,22127293,22127307,22127207,22127431}@student.hcmus.edu.vn

> The order of authors does not reflect the level of contribution.
>
> ⋆ Corresponding author

## Abstract

Event retrieval in large-scale video collections remains a challenging task due to the complexity of multimodal content and the semantic gap between user queries and visual data. In this paper, we present **AIthena-Vision**, our entry for the AI Challenge HCMC 2025 [3], an adaptive system to retrieve large-scale video events designed to effectively address these challenges. The core of our system combines a state-of-the-art Perception Encoder (a CLIP variant) for superior text-visual alignment with an LLM-driven multiperspective query expansion to improve retrieval accuracy. Furthermore, the system incorporates adaptive temporal search and integrates multiple sources of multimodal evidence, including OCR, ASR, and object detection. With an interactive interface that supports collaborative searching, AIthena-Vision offers a competitive and effective retrieval solution. In our latest evaluation with the competition ground truth, the system achieved 92% precision, outperforming our previous system [17] - which obtained the highest score among all teams in the third preliminary round last year - with 81% precision, demonstrating significant improvement and robustness.

**Keywords:** Event retrieval, Perception Encoder, Temporal Search, LLM-based multi-perspective generation.

## 1 Introduction

Images and videos have become essential for capturing everyday activities, from personal memories to major events. These visual records are often stored on social media platforms, video sharing services, and news outlets, leading to vast and diverse collections in terms of size, content, and themes. However, this diversity makes it challenging to accurately retrieve specific images or scenes. Users often wish to locate particular moments based on remembered descriptions or keywords, whether within their personal photos or video archives.

This problem is central to the [AI Challenge 2025 HCMC](https://aichallenge.hochiminhcity.gov.vn/), which adopts a format similar to the Lifelog Search Challenge (LSC) and Video Browser Showdown (VBS). The competition provides a dataset of Vietnamese videos spanning various topics and genres, accompanied by tasks that require participants to identify specific frames from these videos based on input queries (e.g., text descriptions). The primary objectives - accuracy and retrieval speed - are the key aspects that our proposed system aims to enhance.

Our research introduces a tool designed to assist humans in addressing tasks within the competition by employing multiple approaches for information retrieval. Our proposed system follows a two-stage architecture: Data Preprocessing and Retrieval Processing. In the initial stage, the video dataset is processed to extract keyframes (images) together with associated metadata (e.g., named entities). These outputs collectively form the searchable database employed in the Retrieval Processing phase.

This phase consists of two primary components: generating candidate images and validating those candidates. In the first component, the system accepts queries in natural language formats - such as text, speech, or images - and retrieves a ranked list of potentially relevant frames from the database based on semantic similarity and contextual alignment.

The second component focuses on verifying these retrieved candidates through additional re-ranking algorithms and, when necessary, manual inspection. It should be noted that, within the context of the competition, our team manually constructed the query representations and configured the system rather than directly using the full queries provided in the challenge.

To illustrate the practical effectiveness of our tool, we compare our system on both 2024 and 2025 competition datasets, which contributed to achieving a high rank in the preliminary round, along with presenting summary statistics and evaluation results.

## 2 Related Work

With the rising scale of multimedia archives, numerous interactive retrieval systems have been proposed in major evaluation benchmarks [16]. For the Video Browser Showdown [12] (VBS), diveXplore [13] has consistently demonstrated the importance of user-driven interaction, introducing temporal query operators (e.g., `A < B`) and efficient keyframe browsing interfaces. However, its retrieval effectiveness degrades in cases where users formulate complex or ambiguous natural language descriptions.

VideoEase [8] improves search performance by fusing multiple vision-language embeddings (CLIP [10], BLIP2 [7], OpenCLIP [5]) with a reranking strategy and Milvus-based vector indexing for faster operations. Yet, it remains limited to similarity-based retrieval without deeper semantic interpretation or multimodal reasoning.

In the Lifelog Search Challenge (LSC), MEMORIA [4] combines extensive annotation pipeline - including object detection, OCR, and captioning - with LLM-based keyword extraction to support contextual lifelog search. Despite its strong performance, MEMORIA [4] struggles with multi-step temporal reasoning and depends heavily on pre-generated metadata. When critical visual semantics are missing from the annotation stage, retrieval recall drops significantly.

Generative AI has recently been explored to address semantic ambiguity, such as converting an abstract user description into a synthesized image that can serve as a visual query [11]. While promising, current generative retrieval systems still suffer from slow response times, which limits their applicability in interactive competitive environments like IViSE and VBS.

In summary, while existing systems have made significant strides in multimodal indexing and interactive browsing, they frequently specialize in either semantic alignment or temporal interaction - but rarely both. Moreover, the ability to interpret complex, multi-perspective user queries remains largely unaddressed. To bridge these gaps, our system AIthena is designed to provide holistic query understanding through reliable multimodal fusion, deeper semantic reasoning, and flexible multi-step temporal handling.

## 3 AIthena-Vision Architecture

Our system is divided into two main stages: the data processing stage and the retrieval stage (Fig. 1).

![Figure 1. Overall architecture of the AIthena-Vision system, illustrating the two-stage workflow consisting of data processing and retrieval.](aithena_markdown_assets/figure1.png)

*Figure 1. Overall architecture of the AIthena-Vision system, illustrating the two-stage workflow consisting of data processing and retrieval.*

In the data preparation stage, we extract important frames, or keyframes, from the video dataset. These keyframes are then further processed to extract various types of information, including feature vectors, OCR text, audio, and object data. All extracted information is stored in the database to support multimodal retrieval in the next stage.

In the retrieval stage, users can perform searches using multiple modes such as temporal search, semantic search, or text-based search. Additionally, the system integrates a Large Language Model (LLM) that not only expands user queries but also generates multiple semantic variations. This allows the system to better understand user intent and retrieve more accurate and relevant results.

### 3.1 Data Preprocessing

To extract keyframes effectively, our research leverages AutoShot [19], a state-of-the-art model for shot detection. After detecting the shots, we take three representative frames from each one - the first, middle, and last frames.

However, even with this approach, some redundant frames may still remain, meaning frames that look almost identical. To improve efficiency, we use BEiT-3 [18] to extract features from these frames and compare their semantic vectors. Frames with a similarity score above 0.9 are considered too similar and are removed. This ensures that only visually and semantically distinct keyframes are kept for the next steps.

After that, the selected frames are processed and transformed into multiple types of data - such as feature vectors, OCR text, and object information - which are all stored in the database.

### 3.2 Semantic Vector Retrieval

For visual feature extraction, we use PE-core-BigG [2] (OpenCLIP [5]) and BEiT-3 [18]. The PE-core-BigG model is trained on a large-scale dataset and achieves state-of-the-art performance across various vision tasks. Meanwhile, BEiT-3 is particularly strong in aligning text and image features thanks to its masked language modeling across modalities mechanism. Compared to our earlier architectures (e.g., ViT-L/14 [10]), this combination demonstrates enhanced cross-modal alignment accuracy, improved zero-shot generalization for uncommon objects and scenes. It also enables more reliable interpretation of fine-grained spatial relationships, such as “two people in front and one behind.”

User queries are first language-detected and translated into English for standardization. The translated query is then embedded into a vector space by both models (PE-core-BigG and BEiT-3). These embeddings are subsequently ensembled to generate a unified retrieval result.

The ensemble process operates as follows: for each model, the system retrieves the top-k candidates based on cosine similarity. The score of each candidate $s$ is normalized by dividing it by the highest score $s_{\max}$ obtained within that model. After normalization, a weighted aggregation is applied where each model contributes according to its reliability factor (e.g., CLIP: 0.6, BEiT-3: 0.4). If a candidate appears in both result sets, its final score is computed as the sum of the weighted scores from each model:

$$
\mathrm{Score}_{\mathrm{final}}(i)
= w_1 \cdot \frac{s_i^{(1)}}{s_{\max}^{(1)}}
+ w_2 \cdot \frac{s_i^{(2)}}{s_{\max}^{(2)}}.
$$

where $w_1$ and $w_2$ denote the model weights for PE-core-BigG and BEiT-3, respectively.

Finally, all candidates are re-ranked based on their aggregated scores to produce the final retrieval list. This ensemble retrieval mechanism leverages the broad semantic understanding of OpenCLIP and the fine-grained cross-modal reasoning of BEiT-3, thereby achieving both conceptual robustness and detailed visual-textual alignment.

### 3.3 Metadata Retrieval

Beyond visual features, our system also extracts rich metadata from videos. On-screen text is obtained using a hybrid OCR pipeline with PaddleOCR [15] for text detection and VietOCR [9] for recognition. However, since some recognized Vietnamese text may still contain spelling inconsistencies, we additionally apply the [vietnamese-correction-v2](https://huggingface.co/bmd1905/vietnamese-correction-v2) model to ensure that such errors are corrected and the final output is linguistically accurate.

Furthurmore, Audio is transcribed into text using WhisperX [1], preserving temporal alignment between speech and frames, while objects are detected by CoDERT [14] to enrich contextual understanding.

All extracted text data are indexed using an ElasticSearch-based inverted index, enabling millisecond-scale lookup with partial and fuzzy matching. This ensures fast and flexible retrieval from both OCR and speech transcripts, enhancing multimodal search performance.

### 3.4 Adaptive Temporal Search

In the AI Challenge HCMC 2025, many user queries are formulated as temporally ordered event sequences, such as “a person enters a room, then picks up an object.” Ignoring the intended chronological relationships between sub-events often leads to semantically inconsistent results. To address this, our system incorporates an **Adaptive Temporal Search (ATS)** mechanism that performs robust temporal reasoning over retrieved keyframes. Unlike rigid filtering methods, ATS adaptively constructs chronologically valid event sequences by allowing partial matching, enabling retrieval even when some sub-events are missing or visually ambiguous. The ATS workflow, illustrated in Figure 2 and formalized in Algorithm 1, operates through three main stages: independent candidate retrieval, recursive sequence construction under temporal constraints, and scored ranking of valid sequences, as detailed below.

- **Stage 1: Independent Retrieval.** Each sub-query $q_i$ is processed independently to produce a candidate set $C_i = \{f_{i1}, f_{i2}, \ldots, f_{ik}\}$ of keyframes matching the semantic meaning of $q_i$.
- **Stage 2: Temporal Sequence Construction.** Candidate sets are grouped by their source video, and the algorithm recursively explores all frame combinations that satisfy two constraints:

$$
\text{(Sequential Constraint)} \qquad id(f_{i+1}) > id(f_i), \tag{1}
$$

$$
\text{(Proximity Constraint)} \qquad id(f_{i+1}) - id(f_i) \leq \Delta T_{\max}. \tag{2}
$$

Crucially, the algorithm is "adaptive" as it allows skipping certain sub-queries (e.g., $q_i$) and still attempting to match subsequent ones (e.g., $q_{i+1}$), as long as the final sequence meets the $m_{\min}$ threshold. This prunes invalid paths efficiently.

![Figure 2. Workflow of Multiphase Retrieval and Adaptive Temporal Alignment.](aithena_markdown_assets/figure2.png)

*Figure 2. Workflow of Multiphase Retrieval and Adaptive Temporal Alignment.*

- **Stage 3: Scoring and Ranking.** For each valid sequence $s = (f_1, f_2, \ldots, f_m)$ (where $m \geq m_{\min}$), ATS computes a relevance score using a weighted aggregation:

$$
\mathrm{Score}(s) = \frac{1}{m}\sum_{i=1}^{m} w_i \cdot \mathrm{score}(f_i), \tag{3}
$$

where $w_i$ represents the importance weight of sub-query $q_i$. This computed $\mathrm{Score}(s)$ is then assigned to a representative frame (e.g., the middle frame $f_{m/2}$). The final ranked list displays this representative frame, but it is sorted by the new sequence score $\mathrm{Score}(s)$, not its original frame-level score.

**Algorithm 1: Adaptive Temporal Search (ATS)**

```text
Require: Query set Q = {q1, ..., qn}, weights W_weights = {w1, ..., wn},
         candidate results C = {C1, ..., Cn}, temporal window ΔT_max,
         minimum match threshold m_min
Ensure: Ranked list S_ranked of temporally valid keyframes

1:  Initialization: final_results ← ∅
2:  Group all results in C by video ⇒ video_results
3:  for all video ∈ video_results do
4:      if |query_indices_in_video(video)| ≥ m_min then
5:          valid_seqs ← FindTemporalSequences(video, Q, W_weights, ΔT_max, m_min)
6:          Append valid_seqs to final_results
7:      end if
8:  end for
9:  Rank all sequences s ∈ final_results by Score(s) (Eq. 3)
10: S_ranked ← GetRepresentativeFrames(final_results)
11: return S_ranked
```

As shown in Algorithm 1, the recursive subroutine `FindTemporalSequences` iteratively extends each partial path only when the two temporal constraints are satisfied. When a candidate frame violates the ordering or exceeds the temporal window $\Delta T_{\max}$, the branch is immediately pruned. This pruning strategy ensures that the search complexity grows approximately linearly with the number of valid frame transitions, avoiding the combinatorial explosion of full path enumeration.

By explicitly defining the algorithmic flow, temporal constraints, and scoring function, ATS provides a transparent and computationally efficient mechanism for temporal reasoning. Unlike simpler temporal operators found in systems like diveXplore [13], which are often limited to two-event sequences (e.g., `A < B`), our adaptive approach supports multi-step narratives and partial matching. It effectively balances precision and recall, retrieving chronologically consistent multi-event sequences even when parts of the narrative are visually ambiguous or incomplete. The temporal alignment produced by ATS serves as the foundation for the next module, where the Multiperspective LLM-based Search (Section 3.5) further refines semantic interpretation through linguistic diversification.

### 3.5 Multiperspective LLM-based Search

To make our retrieval system more flexible and human-like, we designed a multi-perspective query reasoning module powered by a Large Language Model (LLM), implemented using GPT-4o (Fig. 3). The idea is simple - when users describe what they remember, they often use different words or phrasing to express the same meaning. Instead of relying on a single literal query, the system deligates query into multiple directions. For example, if a user types:

> “two animals walk in front and two behind”

The system does not just take the sentence literally. Instead, it interprets the meaning behind it and generates several possible reformulations, such as:

- “a herd of four animals walking together,”
- “four animals moving in a group along a path,”
- “a small group of animals walking in pairs,”

These variations represent different ways humans might describe the same visual concept. By exploring multiple linguistic perspectives, the system gains a deeper understanding of what the user may be looking for - improving its ability to retrieve relevant scenes, even when the original query is vague or poetic.

**System workflow:** We divide this reasoning process into three simple steps: expanding the query, searching from different perspectives, and combining the results.

First, when the user enters a query, the LLM creates a few alternative versions that carry similar meanings but slightly different wording. These expanded queries allow the system to cover more possible interpretations of what the user actually means.

![Figure 3. Illustration of the Multiperspective LLM-based Search mechanism.](aithena_markdown_assets/figure3.png)

*Figure 3. Illustration of the Multiperspective LLM-based Search mechanism. The Large Language Model (LLM) iteratively refines the initial user query into multiple divergent query perspectives.*

Next, the system searches for each version independently, looking for the most relevant images or frames that match each interpretation. In this way, the system views the same request from multiple angles, much like how different people might understand the same sentence differently.

Finally, all the search results are gathered and merged into a single ranked list. Overlapping results are combined, and the system carefully balances between diversity and accuracy, ensuring that the final output truly reflects what the user intended to find.

### 3.6 User Interface

The **AIthena-Vision User Interface** is engineered to facilitate high-efficiency, human-in-the-loop video retrieval by translating the system’s multi-modal and temporal reasoning capabilities into an intuitive design (Fig. 4). The interface is strategically segmented to support advanced query formulation, contextualized result exploration, and iterative user refinement.

![Figure 4. The Graphical User Interface of the AIthena-Vision system.](aithena_markdown_assets/figure4.png)

*Figure 4. The Graphical User Interface of the AIthena-Vision system.*

**Multi-Modal Query Formulation (Sections A and F):** The Toolbar (A) acts as the central hub for textual query input and modality selection (e.g., PE-Core-bigG, OCR, ASR, or Image Search). The Multi-Query Formulation Panel (F) supports the Adaptive Temporal Search (ATS) paradigm (Sec. 3.4), enabling users to define sequential subqueries ($q_1, q_2, q_3, \ldots$) corresponding to ordered events - thus enforcing temporal constraints during retrieval. The MV toggle activates the Multiperspective LLM-based Search module (Sec. 3.5), which leverages an LLM for semantic query expansion.

**Interactive Refinement and Contextualization (Sections B and D):** The Sidebar (B) provides interactive controls for Top-K filtering and integrates a GPT-4/QA refinement module, allowing users to iteratively clarify abstract query intent before execution. The Contextual Viewer (D) presents surrounding keyframes of each retrieved segment, offering immediate temporal validation of event sequences without full video playback.

**Result Presentation and Temporal Grouping (Section C):** The Display Area (C) visualizes retrieved keyframes under two modes: the standard View and the Group View, which clusters frames from identical segments. This grouping enhances temporal coherence analysis and supports efficient event boundary identification [6].

**Generative Query Input (Section E):** Finally, this module accepts narrative prompts to synthesize novel visual representations from linguistic descriptions, expanding the query space for conceptual and abstract retrieval tasks.

## 4 Experiments

All experiments in this study use the official query set released for the AIC 2025 Video Retrieval Challenge. The dataset consists of 89 real-world search queries covering a broad range of retrieval scenarios, including identifying specific individuals, matching scenes with subtle visual cues, and retrieving conceptually themed content. This diversity makes it a suitable benchmark for evaluating both retrieval accuracy and ranking consistency.

### 4.1 System-Version Comparison

We evaluated two system generations: the Old System (2024) and the Recent System (2025). Both share the same indexing and retrieval pipeline, but the latter integrates improved embedding alignment and a refined scoring mechanism for earlier retrieval of relevant items. Using the official ground truth from the AIC 2025 competition, the Recent System achieved 92% precision, surpassing the Old System’s 81%, which had led all teams in AIC 2024 - demonstrating clear gains in retrieval accuracy and robustness.

Table 1 reports results on the 2024 evaluation set, consisting of 65 queries. It is important to note that the @R@k metrics indicate the percentage of queries for which the correct keyframe appears within the top-k retrieved results, not the precision at exactly rank k.

> **R Ranking:** @R1: correct result appears at rank 1; @R5: correct result appears within ranks 1-5; @R10: within ranks 1-10; @R20: within ranks 1-20; @R50: within ranks 1-50; @R100: within ranks 1-100.

**Table 1. Retrieval performance on the 2024 dataset (65 queries).**

| System | @R1 | @R5 | @R10 | @R20 | @R50 | @R100 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline (2024) | 27.69 | 27.69 | 9.24 | 10.77 | 9.24 | 15.38 |
| AIthena-Vision (Proposed) | 38.46 | 30.77 | 4.62 | 21.54 | 3.08 | 1.54 |

On this dataset, the Recent System shows a clear improvement at the top of the ranking list, with @R1 increasing from 27.69% to 38.46%, demonstrating that the updated scoring mechanism helps the system identify the correct answer more decisively. Improvements at higher @Rk (e.g., @R5 and beyond) should be interpreted as the fraction of queries whose correct keyframe appears within the corresponding top-k positions. This means lower percentages at deeper ranks (e.g., @R50, @R100) do not indicate worse performance, but rather that only a smaller fraction of queries require examining deeper candidates to locate the correct answer.

**Table 2. Retrieval performance on the 2025 dataset (89 queries).**

| System | @R1 | @R5 | @R10 | @R20 | @R50 | @R100 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline (2024) | 15.73 | 19.10 | 5.62 | 22.47 | 20.23 | 16.85 |
| AIthena-Vision (Proposed) | 47.19 | 17.98 | 10.11 | 5.62 | 6.74 | 12.36 |

Table 2 summarizes the evaluation on the full AIC 2025 dataset with 89 queries. The same interpretation applies: improvements are concentrated at early ranks (@R1 and @R5), indicating that the system is highly effective at surfacing the correct keyframe quickly. Lower percentages at deeper ranks reflect that only a few queries require searching further down the ranking to locate the correct result, often due to visually ambiguous scenes or semantically diffuse queries.

To complement the R@k metrics, we further compute Median Rank and Mean Reciprocal Rank (MRR) to evaluate search depth and ranking confidence. On the 2024 dataset, AIthena-Vision reduces the median retrieval depth from 5 to 3 and improves MRR from 0.2 to 0.333, while on the 2025 dataset, the median rank decreases from 16 to 2 and MRR increases from 0.0625 to 0.5. These results indicate that the proposed system returns correct results earlier and with higher ranking confidence. For reproducibility, the full evaluation sheets and computation scripts are publicly available at: https://github.com/NghiaZun/AIthena_Statistic.

Overall, the proposed system demonstrates a substantial improvement in early-rank retrieval precision. In particular, the increase in @R1 and @R5 indicates that the system is more likely to surface the correct result within the top-ranked positions, thereby reducing the average search depth required for successful retrieval. This behavior directly enhances interactive retrieval efficiency, as fewer refinement steps are needed to reach a relevant match.

However, performance at deeper ranks remains variable, particularly in queries involving visually ambiguous scenes or semantically diffuse descriptions. These cases highlight the need for stronger cross-modal disambiguation and enhanced representation robustness, motivating the reranking analysis in the next section.

### 4.2 Comparison Traditional Approach and Multi-View Representation

To understand where the ranking behavior improves or degrades, we conduct a finer-grained reranking experiment on six representative queries selected from the AIC 2025 dataset. These queries were chosen to span different perceptual reasoning challenges (object focus, human activity, environment, composition-based similarity).

The results in Table 3 compare three types of model configurations:

- Single-model baselines (e.g., CLIP-ViT-L/14),
- A stronger pretrained model (PE-Core-bigG-14-448 + BEiT-3),
- A multi-view model ensemble (Perspective), evaluated with different numbers of component embeddings.

The differences between these approaches are clear. CLIP-ViT-L/14 exhibits weak sensitivity to fine-grained visual semantics, often placing the correct answer beyond rank 50. PE-Core-bigG-14-448 + BEiT-3 performs far better and frequently retrieves the correct answer within the Top-20, but its performance fluctuates significantly across queries.

The turning point occurs when multiple embedding representations are fused. The Perspective ($n = 5$) configuration consistently retrieves the correct answer at or near the Top-1, while maintaining stable behavior across all tested queries. This indicates that retrieval quality improves not simply by using a “stronger model,” but by enabling the system to observe the same sample through multiple complementary semantic lenses.

**Table 3. Reranking results on 6 sampled queries from the AIC 2025 set (Top-100 evaluation). A rank of -1 indicates that the correct keyframe does not appear within the Top-100 retrieved results.**

| Model | n | q1 | q2 | q3 | q4 | q5 | q6 |
|---|---:|---:|---:|---:|---:|---:|---:|
| CLIP-ViT-L/14 | - | 32 | -1 | 14 | 98 | 6 | 43 |
| PE-Core-bigG-14-448 + BEiT-3 | - | 1 | 11 | 7 | 13 | 9 | 9 |
| Perspective | 3 | 1 | 3 | 6 | 1 | 14 | 4 |
| Perspective | 5 | 9 | 1 | 1 | 2 | 6 | 1 |
| Perspective | 7 | 4 | 2 | 2 | 3 | 15 | 5 |

In essence, these results demonstrate that retrieval effectiveness hinges not on the power of a single model, but on the complementarity of multiple semantic representations. Our multi-view ensemble consistently outperforms single-model configurations by reducing rank variance and improving stability. While methods like RRF offer some improvement, they lack the semantic reinforcement of our approach. The key insight is that representation diversity is a more critical factor for retrieval reliability than the size or strength of an individual model.

### 4.3 Practical Performance Evaluation

In the Final Round of the AIC 2025 competition, AIthena-Vision achieved an Outstanding overall evaluation by competition’s organizer. Across all four major task categories, our system consistently demonstrated high stability, robustness, and adaptability under strict time constraints and diverse evaluation conditions. The detailed official evaluation is summarized as follows:

- TKIS (Textual Known Item Search): **Excellent**
- VKIS (Video Known Item Search): **Excellent**
- TRAKE (Temporal Retrieval & Alignment of Key Events): **Excellent**
- QA (Question & Answer): **Outstanding**
- Overall Performance: **Outstanding**

A deeper analysis of the Final Round results highlights key factors driving success.

For TKIS and VKIS, performance stemmed from: (i) the enhanced Semantic Vector Retrieval module (Sec. 3.2), fusing PE-Core-BigG and BEiT-3 embeddings to robustly align visual-textual semantics in ambiguous or text-sparse scenes; and (ii) Early-Rank Retrieval Optimization (Sec. 4.1), prioritizing relevant hits to boost precision and cut latency. Complementarily, Metadata Retrieval (Sec. 3.3) exploited OCR/ASR cues as fallback pathways when one modality outperformed others.

In TRAKE, the revamped Interactive Temporal UI (Fig. 6) enabled millisecond-precision event scrutiny, while Adaptive Temporal Search (ATS) (Sec. 3.4) imposed strict sequential constraints - excelling in dense annotations and queries demanding rigorous temporal logic to isolate unique events.

For QA, the ASR-based transcription interface (Fig. 7) was pivotal, surfacing critical speech as text to facilitate swift, accurate extraction - bypassing irrelevant dialogue and mitigating errors in extended videos.

**Real-World Query Examples.** To illustrate the system’s effectiveness, we present three representative queries from the Final Round:

- **TKIS Query:** “The clip shows an exhibition program, beginning with an image of a decorative panel in a royal style, featuring the text ‘PHU XUAN - GIA DINH - NHUNG DAU AN LICH SU’, along with dragon and cloud motifs and two pillars on each side adorned with vibrant patterns, creating the impression of a traditional festival gate.”

![Figure 5. An example of a TKIS QUERY in the AIC 2025 competition.](aithena_markdown_assets/figure5.png)

*Figure 5. An example of a TKIS QUERY in the AIC 2025 competition.*

- **TRAKE Query:** “In a bicycle race stage, the moment 3 cyclists cross the finish line in succession, timed when the wheel is seen crossing the line. (E1): The first cyclist, pink helmet, pink jersey. (E2): The second cyclist, blue helmet. (E3): The third cyclist, red helmet.”

![Figure 6. An example of a TRAKE QUERY in the AIC 2025 competition.](aithena_markdown_assets/figure6.png)

*Figure 6. An example of a TRAKE QUERY in the AIC 2025 competition.*

- **QA Query:** “Identify the name of a world-famous company: The clip shows a large castle perched on a mountaintop, standing out amid lush green forests, with wide plains, a large lake, and distant rolling hills in the background, creating a majestic and poetic scene. This castle also served as the inspiration for the logo of a famous global brand. It is a castle recognized by UNESCO as a World Heritage Site in the Bavaria region of Germany.”

![Figure 7. An example of a QA QUERY in the AIC 2025 competition.](aithena_markdown_assets/figure7.png)

*Figure 7. An example of a QA QUERY in the AIC 2025 competition.*

**Preliminary Conclusion.** The Final Round results confirm AIthena-Vision as a robust and versatile lifelog retrieval system, with its multimodal fusion, temporal precision, and interactive design delivering strong performance across all query types.

## 5 Conclusion

In this work, we introduced AIthena-Vision, a two-stage interactive video event retrieval system designed for large-scale multimodal datasets in the context of the AIC 2025 challenge. By integrating a stronger vision language encoder (PE-Core-bigG-14-448 + BEiT-3) with a multiperspective, LLM-driven query expansion strategy, the system achieves more reliable semantic alignment and significantly improves early-rank retrieval performance. The incorporation of Adaptive Temporal Search further ensures that retrieved results respect event ordering, while multimodal evidence from OCR, ASR, and object detection provides additional contextual grounding. Combined with an intuitive user interface that supports iterative refinement, AIthena-Vision enhances both retrieval accuracy and user efficiency.

Despite these improvements, challenges remain in scenarios involving visually ambiguous scenes, narratively expressed queries, and semantically incomplete metadata. In future work, we plan to explore (1) lightweight domain-specific adapters to specialize embeddings for competition-style video corpora, (2) temporal transformer architectures for modeling motion and scene transitions more effectively, and (3) user-in-the-loop relevance feedback mechanisms to iteratively refine retrieval outcomes in real time. We also aim to further reduce latency in multiperspective query expansion to ensure responsiveness during high-load interactive search sessions.

By combining stronger semantic representations, temporal reasoning, and adaptive multi-view query processing, AIthena-Vision provides a solid foundation for robust large-scale event retrieval and offers promising directions for continued development.

## Acknowledgement

This research is supported by research funding from Faculty of Information Technology, University of Science, Vietnam National University - Ho Chi Minh City.

## References

1. Bain, M., Huh, J., Han, T., Zisserman, A.: **Whisperx: Time-accurate speech transcription of long-form audio.** https://arxiv.org/abs/2303.00747 (2023)
2. Bolya, D., Huang, P.Y., Sun, P., Cho, J.H., Madotto, A., Wei, C., Ma, T., Zhi, J., Rajasegaran, J., Rasheed, H., Wang, J., Monteiro, M., Xu, H., Dong, S., Ravi, N., Li, D., Dollár, P., Feichtenhofer, C.: **Perception encoder: The best visual embeddings are not at the output of the network.** (2025), https://arxiv.org/abs/2504.13181
3. Do, T.L., Huynh, V.T., Nguyen, H.D., Nguyen-Quang, T., Tran, M.K., Nguyen, T.T., Ninh, T.V., Le, T.K., Ngo, T.D., Dang-Nguyen, D.T., Ngo, T.T., Schöffmann, K., Gurrin, C., Tran, M.T.: **Toward abstraction-level event retrieval in large video collections: Leveraging human knowledge and LLM-based reasoning in the Ho Chi Minh City AI Challenge 2025.** In: Proceedings of the 14th International Symposium on Information and Communication Technology (SOICT 2025). CCIS, Springer, Nha Trang, Vietnam (2025)
4. Gago, A., Kaluza, B., Neves, A.J.R.: **Memoria: A memory enhancement and moment retrieval application at the lsc2025.** In: Proceedings of the 8th Annual ACM Workshop on the Lifelog Search Challenge. p. 1-6. LSC ’25, Association for Computing Machinery, New York, NY, USA (2025). https://doi.org/10.1145/3729459.3748693
5. Ilharco, G., Wortsman, M., et al.: **Openclip.** Tech. rep., LAION (2021), https://github.com/mlfoundations/open_clip
6. Jäckl, B., Kruchina, J., Joos, L., Keim, D.A., Peška, L., Lokoč, J.: **Evaluating keyframe layouts for visual known-item search in homogeneous collections.** (2025), https://arxiv.org/abs/2510.04396
7. Li, J., Li, D., Xiong, C., Hoi, S.: **Blip-2: Bootstrapping language-image pretraining with frozen image encoders and large language models.** arXiv preprint arXiv:2301.12597 (2023)
8. Nguyen, T.N., et al.: **Videoease: Multimodal deep embedding fusion for interactive video retrieval.** In: Proc. Int. Conf. on Multimedia Modeling (MMM), VBS Workshop (2025)
9. Phan, Q., Team, V.R.: **Vietocr: Transformer-based ocr for vietnamese text recognition.** (2024), https://github.com/pbcquoc/vietocr
10. Radford, A., Kim, J.W., Hallacy, C., et al.: **Learning transferable visual models from natural language supervision.** In: Proc. Int. Conf. on Machine Learning (ICML) (2021)
11. Ramesh, A., Pachocki, J., et al.: **Hierarchical text-conditional image generation with clip latents.** In: Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR) (2022)
12. Schoeffmann, K., Ahlström, D., Bailer, W., et al.: **The video browser showdown: a live evaluation of interactive video search tools.** International Journal of Multimedia Information Retrieval 3, 113-127 (2014). https://doi.org/10.1007/s13735-013-0050-8
13. Schoeffmann, K., Leopold, M.: **Ai-based video content understanding for automatic and interactive multimedia retrieval.** In: Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition Workshops (CVPRW). IEEE (2025)
14. Swaminathan, R.V., King, B., Strimel, G.P., Droppo, J., Mouchtaris, A.: **Codert: Distilling encoder representations with co-learning for transducer-based speech recognition.** In: Proc. Interspeech (2021), https://arxiv.org/abs/2106.07734
15. Team, P.: **Paddleocr: Practical ultra-lightweight ocr toolkit.** (2025), https://github.com/PaddlePaddle/PaddleOCR
16. Thomee, B., Lew, M.S.: **Interactive search in image retrieval: a survey.** International Journal of Multimedia Information Retrieval 1, 71-86 (2012). https://doi.org/10.1007/s13735-012-0014-4
17. Van Nguyen, T., Duong, N.T., Pham, N.T., Luong, T.X., Bui, D.D.: **An interactive system for visual data retrieval from multimodal input.** In: Huynh, V.N., Honda, K., Le, B., Inuiguchi, M., Huynh, H.T. (eds.) Integrated Uncertainty in Knowledge Modelling and Decision Making. pp. 344-356. Springer Nature Singapore, Singapore (2025)
18. Wang, Z., Li, J., et al.: **Beit-3: Image as a foreign language: Beit pretraining for vision and vision-language tasks.** In: Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR) (2023)
19. Zhu, W., Huang, Y., Xie, X., Liu, W., Deng, J., Zhang, D., Wang, Z., Liu, J.: **Autoshot: A short video dataset and state-of-the-art shot boundary detection.** (2023), https://arxiv.org/abs/2304.06116
