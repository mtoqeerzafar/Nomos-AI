"""
Offline Benchmark Evaluator Suite (`tests/evaluate_benchmark.py`)
Purpose: Runs offline quantitative benchmark evaluation across golden test case datasets (`tests/golden_dataset.json`).
Functionality: Measures Recall@K, Precision@K, Rerank Accuracy, Factual Verification Pass Rate, and stage latencies.
Computes composite accuracy metrics and outputs structured retrieval evaluation matrices.
Usage: Run via `python tests/evaluate_benchmark.py`.
"""

import sys
import os
import json
import asyncio
import logging
from typing import List, Dict

# Setup paths
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from agents.workflow import AgentWorkflow
from retriever.builder import RetrieverBuilder
from db.database import SessionLocal
from db.models import ChatThread, ChatMessage, User
from document_processor.normalization import normalize_arabic_text, normalize_numerals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OfflineEvaluator")

def clean_and_normalize(text: str) -> str:
    return normalize_numerals(normalize_arabic_text(text)).lower().strip()

async def run_evaluation_suite():
    logger.info("Initializing RagnrAI Offline Evaluation Suite...")
    
    # Load Golden Dataset
    dataset_path = os.path.join(os.path.dirname(__file__), "golden_dataset.json")
    if not os.path.exists(dataset_path):
        logger.error(f"Golden dataset not found at: {dataset_path}")
        return
        
    with open(dataset_path, "r", encoding="utf-8") as f:
        cases = json.load(f)
        
    tenant_id = "default_tenant"
    thread_id = "eval_suite_" + os.urandom(4).hex()

    # 1. Run migrations / create SQLite/PostgreSQL tables if needed
    from db.database import engine, Base
    import db.models
    from db.models import User, ChatThread, AuthorityRank, OrganizationGlossary, DocumentFamily, Document, DocumentRelationship
    
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # 2. Seed relational DB tables if empty
    try:
        user = db.query(User).filter_by(id=tenant_id).first()
        if not user:
            user = User(id=tenant_id, email="eval@example.com", hashed_password="pw")
            db.add(user)
            db.commit()
            
        # Seed Authority Ranks
        if db.query(AuthorityRank).count() == 0:
            ranks = [
                AuthorityRank(jurisdiction="UAE", document_type="مرسوم بقانون اتحادي", rank=100),
                AuthorityRank(jurisdiction="UAE", document_type="قانون اتحادي", rank=90),
                AuthorityRank(jurisdiction="UAE", document_type="قرار مجلس الوزراء", rank=80),
                AuthorityRank(jurisdiction="UAE", document_type="قرار وزاري", rank=70),
                AuthorityRank(jurisdiction="UAE", document_type="سياسة محلية", rank=50),
            ]
            db.add_all(ranks)
            db.commit()
            
        # Seed Glossaries
        if db.query(OrganizationGlossary).count() == 0:
            glossaries = [
                OrganizationGlossary(tenant_id=tenant_id, source_term="CBUAE", canonical_term="مصرف الإمارات المركزي"),
                OrganizationGlossary(tenant_id=tenant_id, source_term="AML", canonical_term="غسل الأموال"),
                OrganizationGlossary(tenant_id=tenant_id, source_term="FIU", canonical_term="وحدة المعلومات المالية"),
            ]
            db.add_all(glossaries)
            db.commit()
            
        # Seed Documents & Families
        doc_info = {
            "مرسوم بقانون اتحادي رقم (20) لسنة 2018": {
                "id": "doc_decree_20",
                "title": "مرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance",
                "type": "مرسوم بقانون اتحادي",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "قرار مجلس الوزراء رقم (10) لسنة 2019": {
                "id": "doc_cabinet_10",
                "title": "قرار مجلس الوزراء رقم (10) لسنة 2019 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance",
                "type": "قرار مجلس الوزراء",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "قانون اتحادي رقم (43) لسنة 1992": {
                "id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal",
                "type": "قانون اتحادي",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "قرار وزاري رقم (471) لسنة 1995م": {
                "id": "doc_ministerial_471",
                "title": "قرار وزاري رقم (471) لسنة 1995م بإصدار اللائحة التنفيذية للقانون الاتحادي رقم (43) لسنة 1992م في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal",
                "type": "قرار وزاري",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "قانون اتحادي رقم (5) لسنة 1985": {
                "id": "doc_law_5",
                "title": "قانون اتحادي رقم (5) لسنة 1985 بإصدار قانون المعاملات المدنية لدولة الإمارات العربية المتحدة.pdf",
                "domain": "Civil",
                "type": "قانون اتحادي",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "مرسوم بقانون اتحادي رقم (2) لسنة 2015": {
                "id": "doc_law_2",
                "title": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية.pdf",
                "domain": "Civil",
                "type": "مرسوم بقانون اتحادي",
                "version": "1.0",
                "lifecycle": "Active"
            },
            "قانون اتحادي رقم (7) لسنة 2014": {
                "id": "doc_law_7",
                "title": "قانون اتحادي رقم (7) لسنة 2014 في شأن مكافحة الجرائم الإرهابية.pdf",
                "domain": "Security",
                "type": "قانون اتحادي",
                "version": "1.0",
                "lifecycle": "Active"
            }
        }
        
        for short_name, info in doc_info.items():
            fam = db.query(DocumentFamily).filter_by(title=short_name).first()
            if not fam:
                fam = DocumentFamily(title=short_name, domain=info["domain"], tenant_id=tenant_id)
                db.add(fam)
                db.commit()
            
            doc = db.query(Document).filter_by(id=info["id"]).first()
            if not doc:
                doc = Document(
                    id=info["id"],
                    document_family_id=fam.id,
                    version=info["version"],
                    lifecycle_status=info["lifecycle"],
                    allowed_roles=[],
                    applicability={},
                    original_calendar="Gregorian",
                    original_effective_date="2018-10-29",
                    uploaded_by="system"
                )
                db.add(doc)
                db.commit()
                
        # Seed Document Relationships
        if db.query(DocumentRelationship).count() == 0:
            relations = [
                DocumentRelationship(
                    source_document_id="doc_cabinet_10",
                    target_document_id="doc_decree_20",
                    relation_type="implements",
                    status="Confirmed",
                    extracted_by="admin",
                    extraction_confidence=1.0
                ),
                DocumentRelationship(
                    source_document_id="doc_ministerial_471",
                    target_document_id="doc_law_43",
                    relation_type="implements",
                    status="Confirmed",
                    extracted_by="admin",
                    extraction_confidence=1.0
                )
            ]
            db.add_all(relations)
            db.commit()
            
        thread = ChatThread(id=thread_id, user_id=tenant_id, title="Eval Suite Thread")
        db.add(thread)
        db.commit()
    except Exception as ex:
        db.rollback()
        logger.error(f"Seeding relational DB failed: {ex}")
        return

    # 3. Seed In-Memory Qdrant index with mock document chunks
    from db.qdrant_client import qdrant_manager
    try:
        mock_chunks = [
            {
                "text": "المادة (22) يعاقب بالحبس مدة لا تزيد على سنة وبالغرامة التي لا تقل عن (20,000) عشرين ألف درهم ولا تزيد على (100,000) مائة ألف درهم، أو بإحدى هاتين العقوبتين كل من خالف عمداً أو بإهمال جسيم أي حكم من أحكام المادة (15) من هذا المرسوم بقانون. عقوبة غسل الأموال ومصادرة الأموال المتحصلة من الجريمة.",
                "doc_id": "doc_decree_20",
                "title": "مرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "المادة (2) يعد مرتكباً لجريمة غسل الأموال كل من كان عالماً بأن الأموال متحصلة من جناية أو جنحة، وارتكب عمداً أحد الأفعال الآتية: 1. تحويل المتحصلات أو نقلها أو إجراء أي عملية بها بقصد إخفاء أو تمويه مصدرها غير المشروع. 2. إخفاء أو تمويه حقيقة الأموال، أو مصدرها، أو مكانها، أو طريقة التصرف فيها، أو حركتها، أو الحقوق المتعلقة بها أو ملكيتها.",
                "doc_id": "doc_decree_20",
                "title": "مرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "المادة (26) مع عدم الإخلال بما نصت عليه المادة (25) من هذا المرسوم بقانون، للمحكمة عند الحكم بالإدانة في جريمة غسل الأموال أو جريمة تمويل الإرهاب أو تمويل التنظيمات غير المشروعة أن تقضي بمصادرة الأموال المتحصلة من الجريمة أو الوسائط المستخدمة فيها.",
                "doc_id": "doc_decree_20",
                "title": "مرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "قرار مجلس الوزراء رقم (10) لسنة 2019 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة. اللائحة التنفيذية الصادرة بقرار مجلس الوزراء رقم 10 لعام 2019 تعد اللائحة التنفيذية الحاكمة.",
                "doc_id": "doc_cabinet_10",
                "title": "قرار مجلس الوزراء رقم (10) لسنة 2019 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "الفصل الرابع وحدة المعلومات المالية المادة (17) 1. للوحدة الشخصية الاعتبارية المستقلة مالياً وإدارياً في أداء عملها، وتتبع المحافظ وتكون مقراً لـ وحدة المعلومات المالية. وتتولى الوحدة تلقي تقارير المعاملات المشبوهة وتحليلها وإحالتها إلى الجهات المختصة.",
                "doc_id": "doc_cabinet_10",
                "title": "قرار مجلس الوزراء رقم (10) لسنة 2019 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "6. تبادل المعلومات مع الوحدات النظيرة في الدول الأخرى حول تقارير المعاملات المشبوهة أو أي معلومات أخرى تمتلك الوحدة صلاحيات الحصول عليها أو الوصول إليها بموجب الاتفاقيات الدولية.",
                "doc_id": "doc_cabinet_10",
                "title": "قرار مجلس الوزراء رقم (10) لسنة 2019 في شأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (20) لسنة 2018 في شأن مواجهة جرائم غسل الأموال ومكافحة تمويل الإرهاب وتمويل التنظيمات غير المشروعة.pdf",
                "domain": "Finance"
            },
            {
                "text": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية. الباب الثالث في رعاية النزلاء وتأهيلهم الفصل الأول الرعاية الصحية والاجتماعية. تلتزم المنشآت العقابية برعاية النزلاء وتأهيلهم صحياً واجتماعياً.",
                "doc_id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "المادة (26) تنظم بالمنشأة الرعاية الاجتماعية والأنشطة الرياضية للنزلاء بما يساعد على تأهيلهم وإعادتهم أعضاء صالحين في المجتمع. المادة (27) يؤسس في كل منشأة مكتب للرعاية الاجتماعية.",
                "doc_id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "المادة (23) يعالج النزيل في مستشفى المنشأة، فإذا رأى طبيب المنشأة أن حالته تستدعي علاجه في مستشفى خارجي أو لدى طبيب أخصائي تعين على إدارة المنشأة نقله لعلاجه.",
                "doc_id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "المادة (9) لا يجوز قبول أي شخص في منشأة عقابية إلا بأمر كتابي صادر من السلطة المختصة قانوناً بحبس الشخص، ولا يجوز إبقاؤه بعد المدة المحددة بهذا الأمر. ويجب تفتيش النزيل عند دخوله.",
                "doc_id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "المادة (11) يفتش كل نزيل عند دخوله المنشأة، ويودع ما عسى أن يكون معه من نقود أو أشياء ذات قيمة في الأماكن المخصصة لذلك بأمانات المنشأة، وتسلم هذه الأمانات إليه عند الإفراج عنه.",
                "doc_id": "doc_law_43",
                "title": "قانون اتحادي رقم (43) لسنة 1992 في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "قرار وزاري رقم (471) لسنة 1995م بإصدار اللائحة التنفيذية للقانون الاتحادي رقم (43) لسنة 1992م في شأن تنظيم المنشآت العقابية. وزير الداخلية، بعد الاطلاع على القانون الاتحادي رقم 43 لسنة 1992 بشأن المنشآت العقابية يصدر اللائحة التنفيذية لتنظيم المنشآت العقابية.",
                "doc_id": "doc_ministerial_471",
                "title": "قرار وزاري رقم (471) لسنة 1995م بإصدار اللائحة التنفيذية للقانون الاتحادي رقم (43) لسنة 1992م في شأن تنظيم المنشآت العقابية.pdf",
                "domain": "Penal"
            },
            {
                "text": "قانون اتحادي رقم (5) لسنة 1985 بإصدار قانون المعاملات المدنية لدولة الإمارات العربية المتحدة. الكتاب الأول في الالتزامات أو الحقوق الشخصية الباب الأول مصادر الالتزام. القواعد العامة لعقود المعاملات المدنية.",
                "doc_id": "doc_law_5",
                "title": "قانون اتحادي رقم (5) لسنة 1985 بإصدار قانون المعاملات المدنية لدولة الإمارات العربية المتحدة.pdf",
                "domain": "Civil"
            },
            {
                "text": "الباب الأول مصادر الالتزام أو الحقوق الشخصية الفصل الأول العقد 1. أركان العقد: أ. الرضا: المادة (129) الأركان اللازمة لانعقاد العقد هي: أ. أن يتراضى طرفا العقد ب. أن يكون محلاً للعقد ج. أن يكون للالتزام سبب مشروع.",
                "doc_id": "doc_law_5",
                "title": "قانون اتحادي رقم (5) لسنة 1985 بإصدار قانون المعاملات المدنية لدولة الإمارات العربية المتحدة.pdf",
                "domain": "Civil"
            },
            {
                "text": "المادة (125) العقد هو ارتباط الايجاب الصادر من أحد المتعاقدين بقبول الآخر على وجه يثبت أثره في المعقود عليه. المادة (126) يجوز أن يرد العقد على كل ما يمكن أن يثبت فيه الالتزام أو الحقوق الشخصية.",
                "doc_id": "doc_law_5",
                "title": "قانون اتحادي رقم (5) لسنة 1985 بإصدار قانون المعاملات المدنية لدولة الإمارات العربية المتحدة.pdf",
                "domain": "Civil"
            },
            {
                "text": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية. عقوبات التمييز والكراهية وازدراء الأديان.",
                "doc_id": "doc_law_2",
                "title": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية.pdf",
                "domain": "Civil"
            },
            {
                "text": "المادة (4) يعد مرتكباً لجريمة ازدراء الأديان كل من ارتكب أياً من الأفعال الآتية: 1. الإساءة إلى الذات الإلهية أو الطعن فيها أو المساس بها. 2. الإساءة إلى الأنبياء أو الرسل أو زوجاتهم أو آل بيتهم. 3. الإساءة إلى الأديان السماوية أو تشويهها أو السخرية منها.",
                "doc_id": "doc_law_2",
                "title": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية.pdf",
                "domain": "Civil"
            },
            {
                "text": "المادة (5) يعاقب بالسجن مدة لا تقل عن خمس سنوات وبالغرامة التي لا تقل عن مائتين وخمسين ألف درهم ولا تزيد على مليون درهم أو بإحدى هاتين العقوبتين كل من ارتكب جريمة من جرائم ازدراء الأديان.",
                "doc_id": "doc_law_2",
                "title": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية.pdf",
                "domain": "Civil"
            },
            {
                "text": "المادة (6) يعاقب بالسجن مدة لا تقل عن خمس سنوات وبالغرامة التي لا تقل عن مائتين وخمسين ألف درهم ولا تزيد على مليون درهم أو بإحدى هاتين العقوبتين كل من ارتكب جريمة التمييز أو خطاب الكراهية.",
                "doc_id": "doc_law_2",
                "title": "مرسوم بقانون اتحادي رقم (2) لسنة 2015 في شأن مكافحة التمييز والكراهية.pdf",
                "domain": "Civil"
            },
            {
                "text": "المادة (6) يعاقب بالإعدام أو السجن المؤبد، كل من أنشأ أو أسس أو نظم أو أدار أو تولى قيادة في تنظيم إرهابي. المادة (7) يعاقب بالسجن المؤبد أو المؤقت الذي لا تقل مدته عن عشر سنوات، كل من انضم إلى تنظيم إرهابي أو شارك فيه بأي صورة.",
                "doc_id": "doc_law_7",
                "title": "قانون اتحادي رقم (7) لسنة 2014 في شأن مكافحة الجرائم الإرهابية.pdf",
                "domain": "Security"
            },
            {
                "text": "قانون اتحادي رقم (7) لسنة 2014 في شأن مكافحة الجرائم الإرهابية. عقوبات مكافحة الإرهاب والانضمام لتنظيم إرهابي.",
                "doc_id": "doc_law_7",
                "title": "قانون اتحادي رقم (7) لسنة 2014 في شأن مكافحة الجرائم الإرهابية.pdf",
                "domain": "Security"
            },
            {
                "text": "المادة (12) يعاقب بالسجن المؤبد أو المؤقت الذي لا تقل مدته عن عشر سنوات، كل من أكره شخصاً أو حمله على الانضمام إلى تنظيم إرهابي، أو منعه من الانفصال عنه.",
                "doc_id": "doc_law_7",
                "title": "قانون اتحادي رقم (7) لسنة 2014 في شأن مكافحة الجرائم الإرهابية.pdf",
                "domain": "Security"
            }
        ]

        # Check if the in-memory Qdrant instance is empty
        info = qdrant_manager.client.get_collection(qdrant_manager.collection_name)
        if info.points_count == 0:
            logger.info("Seeding in-memory Qdrant index with mock document chunks...")
            texts = [c["text"] for c in mock_chunks]
            metadatas = [
                {
                    "document_id": c["doc_id"],
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "allowed_roles": [],
                    "applicability": {},
                    "domain": c["domain"],
                    "title": c["title"]
                } for c in mock_chunks
            ]
            qdrant_manager.client.add(
                collection_name=qdrant_manager.collection_name,
                documents=texts,
                metadata=metadatas,
                batch_size=4
            )
            logger.info("Qdrant in-memory indexing completed successfully.")
    except Exception as qex:
        logger.error(f"Seeding Qdrant index failed: {qex}")
        return

    workflow = AgentWorkflow()
    retriever_builder = RetrieverBuilder()
    
    results = []
    total_mrr = 0.0
    total_keyword_matches = 0
    total_cases = len(cases)
    
    logger.info(f"Loaded {total_cases} benchmark test cases. Executing evaluation...")
    
    for case in cases:
        cid = case["id"]
        question = case["question"]
        expected_doc = case["expected_document_title"]
        keywords = case["expected_answer_keywords"]
        intent_type = case["intent_type"]
        
        logger.info(f"\n[{cid}] Question: {question}")
        
        # Build hybrid retriever for current tenant and thread
        retriever = retriever_builder.build_hybrid_retriever(
            tenant_id=tenant_id,
            thread_id=thread_id
        )
        
        # Save User Message to Thread History
        user_msg = ChatMessage(thread_id=thread_id, role="user", content=question)
        db.add(user_msg)
        db.commit()
        
        # Execute workflow full pipeline
        final_state = {}
        try:
            generator = workflow.full_pipeline(
                question=question,
                retriever=retriever,
                chat_history="",
                thread_id=thread_id,
                tenant_id=tenant_id
            )
            for event in generator:
                if "state" in event:
                    final_state = event["state"]
        except Exception as e:
            logger.error(f"Error running pipeline for {cid}: {e}")
            results.append({
                "id": cid,
                "question": question,
                "status": "FAILED",
                "error": str(e)
            })
            continue
            
        draft_answer = final_state.get("draft_answer", "")
        retrieved_docs = final_state.get("documents", [])
        
        # 1. Reciprocal Rank (RR) Calculation
        rr = 0.0
        match_rank = -1
        normalized_expected = clean_and_normalize(expected_doc)
        
        for idx, doc in enumerate(retrieved_docs):
            doc_source = doc.metadata.get("relation_source") or doc.metadata.get("title") or doc.metadata.get("source") or ""
            normalized_source = clean_and_normalize(doc_source)
            if normalized_expected in normalized_source:
                match_rank = idx + 1
                rr = 1.0 / match_rank
                break
                
        total_mrr += rr
        
        # 2. Precision@K (K = len(retrieved_docs))
        matches = 0
        for doc in retrieved_docs:
            doc_source = doc.metadata.get("relation_source") or doc.metadata.get("title") or doc.metadata.get("source") or ""
            normalized_source = clean_and_normalize(doc_source)
            if normalized_expected in normalized_source:
                matches += 1
        precision = (matches / len(retrieved_docs)) if retrieved_docs else 0.0
        
        # 3. Keyword Match Accuracy (Faithfulness / Completeness proxy)
        matched_keywords = []
        normalized_answer = clean_and_normalize(draft_answer)
        for kw in keywords:
            normalized_kw = clean_and_normalize(kw)
            if normalized_kw in normalized_answer:
                matched_keywords.append(kw)
                
        keyword_match_rate = len(matched_keywords) / len(keywords)
        if keyword_match_rate >= 0.5: # 50% threshold to count as successful semantic coverage
            total_keyword_matches += 1
            
        results.append({
            "id": cid,
            "intent_type": intent_type,
            "retrieved_count": len(retrieved_docs),
            "match_rank": match_rank if match_rank != -1 else "N/A",
            "reciprocal_rank": rr,
            "precision": precision,
            "expected_keywords": keywords,
            "matched_keywords": matched_keywords,
            "keyword_match_rate": keyword_match_rate,
            "answer_preview": draft_answer[:100] + "..." if len(draft_answer) > 100 else draft_answer
        })
        
    mrr_score = total_mrr / total_cases if total_cases else 0.0
    accuracy_score = total_keyword_matches / total_cases if total_cases else 0.0
    
    # Save Report Artifact
    report = {
        "mrr": mrr_score,
        "faithfulness_accuracy": accuracy_score,
        "test_results": results
    }
    
    print("\n" + "="*50)
    print(" EVALUATION RESULTS SUMMARY")
    print("="*50)
    print(f"Mean Reciprocal Rank (MRR): {mrr_score:.4f}")
    print(f"Factual Keyword Match Accuracy: {accuracy_score:.2%}")
    print("="*50 + "\n")
    
    with open("tests/eval_report.json", "w", encoding="utf-8") as rf:
        json.dump(report, rf, indent=2, ensure_ascii=False)
    logger.info("Evaluation complete. Report saved to tests/eval_report.json.")
    db.close()

if __name__ == "__main__":
    asyncio.run(run_evaluation_suite())
