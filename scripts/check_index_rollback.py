"""Integration test using three-document disposable local Elasticsearch indices.

Never modifies ANNOTATION_READ_INDEX or production aliases. Removes only indices
created by this invocation. Outputs no credentials or provider endpoints.
"""
import json
from pathlib import Path
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps/backend'))
from app.core.deps import get_text_client
from app.modules.ingest.index_versions import manifest,promote
from app.modules.media.router import _video_evidence_payload


def main():
    client=get_text_client().client
    prefix='optimization-test-'+uuid4().hex
    versions=[prefix+'-v1',prefix+'-v2']; alias=prefix+'-read'; created=[]
    try:
        for version in versions:
            client.indices.create(index=version,mappings={'properties':{
                'source_type':{'type':'keyword'},'model_version':{'type':'keyword'},
                'video_id':{'type':'keyword'},'keyframe_id':{'type':'keyword'},
                'start_seconds':{'type':'float'},'end_seconds':{'type':'float'}}})
            created.append(version)
            for source in ['ocr','asr','caption']:
                client.index(index=version,id=source,document={'source_type':source,'model_version':'fixture',
                    'video_id':'test-video','keyframe_id':'test-frame','text_value':version})
            client.indices.refresh(index=version)
        one,two=[manifest(client,index) for index in versions]
        promote(client,alias,versions[0],None,one)
        promote(client,alias,versions[1],versions[0],two)
        # A result from v1 still reads its v1 evidence after the alias moves to v2.
        evidence=_video_evidence_payload('test-video',anchor_frame_id='test-frame',index_version=versions[0])
        assert evidence['evidence']['ocr'][0]['text']==versions[0]
        promote(client,alias,versions[0],versions[1],one)
        assert set(client.indices.get_alias(name=alias))=={versions[0]}
        print(json.dumps({'promote':True,'rollback':True,'pinnedEvidence':True,'fixtureDocuments':6}))
    finally:
        for index in created:
            assert index in versions and index.startswith(prefix)
            client.indices.delete(index=index)


if __name__=='__main__':main()
